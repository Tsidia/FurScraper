"""Local web UI: configuration, run control and gallery in one browser tab.

Runs a loopback HTTP server and opens the browser at it. The page is the app
window: it heartbeats while open, and the process exits shortly after the tab
closes, so nothing is left running in the background.

The address is deliberately stable (fixed port, persistent key) so it can be
bookmarked and reopened like any other page, rather than the app having to
install shortcuts to make itself findable.

Security notes, because this server can read and write credentials:

  * Bound to 127.0.0.1 unless the user turns on network access, which widens
    the bind to the LAN but never changes who is allowed in.
  * Every request must be authorized: the access key (lives in %APPDATA%, is
    passed in the launch URL, and old bookmarks still carry it) or a paired
    device's session cookie. Without one, any website you happen to have open
    could POST to this port; CORS does not stop a request being *sent*, only
    the reading of its response. Session cookies are SameSite=Lax for the same
    reason.
  * Host/Origin are validated against an allowlist to blunt DNS-rebinding,
    where a hostile domain re-resolves to 127.0.0.1 (or the LAN address) to
    talk to local services. With LAN access on, the list grows to this
    machine's names and addresses, never wildcards.
  * New devices join through short-lived 6-digit pairing codes (see
    pairing.py), so the key itself never has to be typed or sent to a phone.
  * The host machine is exempt from pairing: a request whose true client
    address is one of this machine's own addresses gets the page and a
    session directly, because "authenticate to yourself" is a wall with no
    door on either side of it. Remote addresses always pair.

The key persisting on disk is a deliberate trade for the stable URL: anything
able to read it can already read config.json, which holds the actual site
credentials.

When network access is on, the Tsidia hub (tsidia_hub.py) may also run in this
process, giving the app its friendly address: http://<name>.local/furscraper/.
The hub strips the /furscraper prefix before proxying, so this server sees
normal rooted paths either way; the UI uses relative URLs so the page works at
both addresses.
"""
import io
import json
import mimetypes
import os
import re
import secrets
import socket
import sys
import threading
import time
import webbrowser
from http.cookies import SimpleCookie
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen

import gallery
import netutil
import pairing
import scheduler
import tsidia_hub
from common import APPDATA, LOG_PATH, UI_KEY_PATH, load_config, save_config

HEARTBEAT_GRACE = 90      # seconds without a heartbeat before shutting down
PAGE_SIZE = 120
DEFAULT_PORT = 47821


def load_or_create_key():
    """The UI access key, stable across launches so the URL can be bookmarked."""
    try:
        key = UI_KEY_PATH.read_text(encoding="utf-8").strip()
        if key:
            return key
    except OSError:
        pass
    key = secrets.token_urlsafe(24)
    UI_KEY_PATH.write_text(key, encoding="utf-8")
    return key


def _resource_dir():
    """UI assets live beside this file, or in PyInstaller's extraction dir."""
    base = getattr(sys, "_MEIPASS", None)
    return Path(base) / "ui" if base else Path(__file__).resolve().parent / "ui"


class RunState:
    """Shared state for the currently running (or last) scrape."""

    def __init__(self):
        self.lock = threading.Lock()
        self.running = False
        self.message = "Idle."
        self.finished_at = None
        self.result = None      # (new, errors, failures)
        self.error = None

    def snapshot(self):
        with self.lock:
            return {
                "running": self.running,
                "message": self.message,
                "finished_at": self.finished_at,
                "result": self.result,
                "error": self.error,
            }

    def set(self, **kw):
        with self.lock:
            for k, v in kw.items():
                setattr(self, k, v)


class App:
    def __init__(self):
        self.token = load_or_create_key()
        self.run_state = RunState()
        self.last_beat = time.time()
        self.httpd = None
        self.port = None
        self.wanted_port = None   # the bookmarkable one, if we got it
        self.port_warning = None  # set when we had to fall back
        self.keep_running = True  # stay up after the tab closes
        self.lan_enabled = False
        self.hub_name = "tsidia"
        self.hub = None           # tsidia_hub.Hub when LAN access is on
        self.firewall = None      # None | ok | missing | pending | declined | failed | unknown
        self.rebind = None        # set by a save that changes network settings
        self._shutdown = threading.Event()

    def url(self):
        return f"http://127.0.0.1:{self.port}/?k={self.token}"

    def network(self):
        """The network block of /api/state. Everything here is cached or
        in-memory; the UI polls state every two seconds."""
        if not self.lan_enabled:
            return {"lan_enabled": False}
        ips = netutil.lan_ips()
        hub = self.hub.status() if self.hub else None
        hub_url = None
        if hub and hub["port"] and hub["role"] in ("leader", "client"):
            suffix = "" if hub["port"] == 80 else f":{hub['port']}"
            hub_url = f"http://{self.hub_name}.local{suffix}/furscraper/"
        return {
            "lan_enabled": True,
            "direct_urls": [f"http://{ip}:{self.port}/" for ip in ips],
            "hub": hub,
            "hub_url": hub_url,
            "firewall": self.firewall,
            "paired": pairing.count(),
        }

    def pair_target(self):
        """The URL a new device should open, numeric so it resolves even where
        .local names do not (older Android, mainly)."""
        ip = netutil.primary_ip()
        if not ip:
            return None
        hub = self.hub.status() if self.hub else None
        if hub and hub["port"] and hub["role"] in ("leader", "client"):
            suffix = "" if hub["port"] == 80 else f":{hub['port']}"
            return f"http://{ip}{suffix}/furscraper/"
        return f"http://{ip}:{self.port}/"


APP = App()


# ---------- the scrape, off the request thread ----------

def _start_run(cfg):
    import logging

    import scraper

    def worker():
        logging.basicConfig(
            filename=LOG_PATH,
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(message)s",
        )
        logger = logging.getLogger()
        logger.info("=== Manual run start ===")
        try:
            new, errs, failures = scraper.run_all(
                cfg, logger, progress=lambda m: APP.run_state.set(message=m)
            )
            APP.run_state.set(
                running=False,
                message="Done.",
                result={"new": new, "errors": errs, "failures": failures},
                error=None,
                finished_at=time.time(),
            )
            logger.info(f"Manual run done: {new} new, {errs} errors")
        except Exception as e:
            logger.exception("Manual run failed")
            APP.run_state.set(
                running=False, message="Failed.", error=str(e), finished_at=time.time()
            )

    APP.run_state.set(running=True, message="Starting…", result=None, error=None)
    threading.Thread(target=worker, daemon=True).start()


# ---------- config validation ----------

FA_KEYS = ("fa_artists", "fa_watchlist", "fa_search")
FA_LABELS = {
    "fa_artists": "FA Artists",
    "fa_watchlist": "FA Watchlist",
    "fa_search": "FA Search",
}


def validate(cfg):
    """Returns a list of human-readable problems; empty means good to save."""
    problems = []
    if not str(cfg.get("output_dir", "")).strip():
        problems.append("Choose an output folder.")
    try:
        if int(cfg.get("interval_minutes", 0)) < 1:
            problems.append("Run interval must be at least 1 minute.")
    except (TypeError, ValueError):
        problems.append("Run interval must be a whole number of minutes.")

    mods = cfg.get("modules", {})
    e6 = mods.get("e621", {})
    if e6.get("enabled") and (not e6.get("username") or not e6.get("api_key")):
        problems.append("e621 is enabled but the username or API key is empty.")

    fa_on = [k for k in FA_KEYS if mods.get(k, {}).get("enabled")]
    auth = cfg.get("fa_auth", {})
    if fa_on and (not auth.get("cookie_a") or not auth.get("cookie_b")):
        names = ", ".join(FA_LABELS[k] for k in fa_on)
        problems.append(
            f"{names} enabled, but the FA cookies are empty. Every FurAffinity "
            "request is authenticated with your own session."
        )

    if cfg.get("lan_enabled"):
        name = str(cfg.get("hub_name") or "").strip().lower()
        if not re.fullmatch(r"[a-z0-9]([a-z0-9-]{0,30}[a-z0-9])?", name):
            problems.append(
                "Network name must be letters, digits and hyphens; it becomes "
                "the <name>.local address."
            )
    return problems


# ---------- HTTP ----------

def _split_host(value):
    """Hostname out of a Host header, lowercased, brackets and port dropped."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:].partition("]")[0]
    return value.partition(":")[0]


def _allowed_hosts():
    """Names this server may be legitimately addressed by. Loopback always;
    with LAN access on, also this machine's own names and addresses."""
    allowed = {"127.0.0.1", "localhost", "::1"}
    if APP.lan_enabled:
        allowed.add(f"{APP.hub_name}.local")
        hostname = socket.gethostname().lower()
        allowed.update({hostname, f"{hostname}.local"})
        allowed.update(netutil.lan_ips())
    return allowed


class Handler(BaseHTTPRequestHandler):
    server_version = "FurScraper"

    def log_message(self, fmt, *args):
        pass  # no console to log to

    # -- helpers --

    def _json(self, obj, status=200, set_cookie=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        if set_cookie:
            self.send_header("Set-Cookie", pairing.cookie_header(set_cookie))
        self.end_headers()
        self.wfile.write(body)

    def _body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            return {}

    def _host_ok(self):
        """Reject anything not addressed to a name this machine answers to,
        and any cross-origin caller. Blunts DNS rebinding, which matters more
        once the server is reachable at a LAN address."""
        if _split_host(self.headers.get("Host")) not in _allowed_hosts():
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                oh = (urlparse(origin).hostname or "").lower()
            except ValueError:
                return False
            if oh not in _allowed_hosts():
                return False
        return True

    def _token_ok(self, query):
        supplied = (
            self.headers.get("X-FurScraper-Token")
            or query.get("k", [""])[0]
            or query.get("token", [""])[0]  # older bookmarks
        )
        return secrets.compare_digest(supplied or "", APP.token)

    def _cookie_token(self):
        raw = self.headers.get("Cookie")
        if not raw:
            return ""
        try:
            jar = SimpleCookie(raw)
        except Exception:
            return ""
        morsel = jar.get(pairing.COOKIE_NAME)
        return morsel.value if morsel else ""

    def _authed(self, query):
        """The master key (header or URL) or a paired device's cookie."""
        return self._token_ok(query) or pairing.valid(self._cookie_token())

    def _client_ip(self):
        """The real client address. That is the socket peer, except when the
        Tsidia hub on this machine is relaying: then its X-Forwarded-For is
        authoritative. Only a loopback peer may speak for someone else, and
        the hub strips the header from incoming requests, so a remote caller
        cannot forge it."""
        peer = self.client_address[0]
        if peer.startswith("127.") or peer == "::1":
            forwarded = self.headers.get("X-Forwarded-For")
            if forwarded:
                return forwarded.split(",")[0].strip()
        return peer

    def _is_host_machine(self):
        """True when the request comes from the machine FurScraper runs on,
        whichever of its own addresses it arrived through: loopback, the LAN
        address, or the friendly name (which resolves to the LAN address)."""
        ip = self._client_ip()
        return ip.startswith("127.") or ip == "::1" or ip in netutil.lan_ips()

    # -- routing --

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        if not self._host_ok():
            self.send_error(403, "Forbidden")
            return

        # Stylesheet and script are requested by the browser itself, which
        # cannot attach a token, and they contain nothing worth protecting;
        # the pairing page needs them too. Everything that touches config,
        # media or credentials is guarded below.
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return

        if path == "/api/ping":
            # Unauthenticated on purpose: a second launch uses it to tell "our
            # server" from "some other program squatting on the port", and the
            # Tsidia hub uses it for liveness. It reveals only the app's name.
            self._json({"app": "FurScraper"})
            return

        if path == "/":
            self._serve_root(query)
            return

        if not self._authed(query):
            self.send_error(403, "Not authorized")
            return

        if path == "/api/state":
            self._api_state()
        elif path == "/api/log":
            self._api_log(query)
        elif path == "/api/gallery":
            self._api_gallery(query)
        elif path == "/api/pair/qr.svg":
            self._api_pair_qr()
        elif path.startswith("/media/"):
            gallery.serve_file(self, unquote(path[len("/media/"):]))
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._host_ok():
            self.send_error(403, "Forbidden")
            return

        path = parsed.path
        if path == "/api/pair":
            # The one unauthenticated POST: an unpaired device redeeming a
            # code. pairing.redeem burns attempts, so it rate-limits itself.
            self._api_pair()
            return

        if not self._authed(query):
            self.send_error(403, "Forbidden")
            return

        if path == "/api/heartbeat":
            APP.last_beat = time.time()
            self._json({"ok": True})
        elif path == "/api/config":
            self._api_save_config()
        elif path == "/api/run":
            self._api_run()
        elif path == "/api/open-folder":
            self._api_open_folder()
        elif path == "/api/pair/new":
            self._api_pair_new()
        elif path == "/api/network/forget":
            self._json({"ok": True, "forgotten": pairing.forget_all()})
        elif path == "/api/network/firewall":
            _fix_firewall_async()
            self._json({"ok": True})
        elif path == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=_shutdown, daemon=True).start()
        else:
            self.send_error(404, "Not found")

    # -- handlers --

    def _serve_root(self, query):
        """The address bar stays clean: a visit that proves itself (master key
        or pairing code) gets the app plus a session cookie, and the page
        strips the query client-side. Anything else gets the pairing page,
        never a bare 403, because a person is looking at this response."""
        code = query.get("pair", [""])[0]
        if code:
            token, error = pairing.redeem(code, self.headers.get("User-Agent", ""))
            if token:
                self._serve_index(set_cookie=token)
            else:
                self._serve_pair(error)
            return
        if self._token_ok(query):
            # A key bookmark keeps working, and quietly upgrades this browser
            # to a cookie so the clean URL can be bookmarked instead.
            cookie = None
            if not pairing.valid(self._cookie_token()):
                cookie = pairing.create(self.headers.get("User-Agent", ""))
            self._serve_index(set_cookie=cookie)
            return
        if pairing.valid(self._cookie_token()):
            self._serve_index()
            return
        if self._is_host_machine():
            # The device hosting the server never pairs with itself: typing
            # the friendly address into the host's own browser just works.
            # Only the page is granted this way; the API still wants the key
            # or a cookie, and the page it serves carries both.
            cookie = pairing.create(
                (self.headers.get("User-Agent", "")[:90] + " (this computer)")
            )
            self._serve_index(set_cookie=cookie)
            return
        self._serve_pair()

    def _serve_html(self, body, set_cookie=None, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        if set_cookie:
            self.send_header("Set-Cookie", pairing.cookie_header(set_cookie))
        self.end_headers()
        self.wfile.write(body)

    def _serve_index(self, set_cookie=None):
        html = (_resource_dir() / "index.html").read_text(encoding="utf-8")
        # Placeholder is distinct from the JS global's name (__TOKEN__): replace
        # is global, so a shared spelling would rewrite the identifier too.
        html = html.replace("__TOKEN_VALUE__", APP.token)
        self._serve_html(html.encode("utf-8"), set_cookie=set_cookie)

    def _serve_pair(self, error=None):
        html = (_resource_dir() / "pair.html").read_text(encoding="utf-8")
        html = html.replace("__ERROR__", error or "")
        self._serve_html(html.encode("utf-8"))

    def _serve_static(self, name):
        if "/" in name or "\\" in name or ".." in name:
            self.send_error(404, "Not found")
            return
        path = _resource_dir() / name
        if not path.is_file():
            self.send_error(404, "Not found")
            return
        ctype = mimetypes.guess_type(name)[0] or "application/octet-stream"
        data = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _api_state(self):
        cfg = load_config()
        scheduled, sched_detail = scheduler.status()
        out_dir = Path(cfg.get("output_dir", ""))
        try:
            file_count = sum(1 for _ in gallery.list_entries(out_dir))
        except Exception:
            file_count = 0
        self._json(
            {
                "config": cfg,
                "run": APP.run_state.snapshot(),
                "scheduled": scheduled,
                "schedule_detail": sched_detail,
                "file_count": file_count,
                "output_exists": out_dir.exists(),
                "url": APP.url(),
                "port": APP.port,
                "network": APP.network(),
                "port_warning": APP.port_warning,
                # Always-on only actually holds once the task exists to trigger
                # it at log-in, so report the combination, not the wish.
                "ui_always_on": bool(cfg.get("keep_ui_running", True)) and scheduled,
                "paths": {
                    "config": str(APPDATA / "config.json"),
                    "log": str(LOG_PATH),
                    "appdata": str(APPDATA),
                },
            }
        )

    def _api_save_config(self):
        cfg = self._body()
        problems = validate(cfg)
        if problems:
            self._json({"ok": False, "problems": problems}, status=400)
            return
        cfg["hub_name"] = str(cfg.get("hub_name") or "tsidia").strip().lower()
        save_config(cfg)
        problems = []
        try:
            scheduler.register(cfg["interval_minutes"])
            scheduled = True
        except Exception as e:
            scheduled = False
            problems.append(f"Saved, but scheduling failed: {e}")
        _apply_network_settings(cfg)
        self._json(
            {"ok": True, "saved": True, "scheduled": scheduled, "problems": problems}
        )

    def _api_run(self):
        if APP.run_state.snapshot()["running"]:
            self._json({"ok": False, "problems": ["A run is already in progress."]}, 409)
            return
        cfg = self._body() or load_config()
        problems = validate(cfg)
        if problems:
            self._json({"ok": False, "problems": problems}, status=400)
            return
        save_config(cfg)
        _start_run(cfg)
        self._json({"ok": True})

    def _api_log(self, query):
        try:
            n = min(int(query.get("tail", ["200"])[0]), 2000)
        except ValueError:
            n = 200
        try:
            lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()
        except FileNotFoundError:
            lines = []
        self._json({"lines": lines[-n:]})

    def _api_gallery(self, query):
        cfg = load_config()
        out_dir = Path(cfg.get("output_dir", ""))
        source = (query.get("source", ["all"])[0] or "all").lower()
        try:
            page = max(1, int(query.get("page", ["1"])[0]))
        except ValueError:
            page = 1
        entries = gallery.list_entries(out_dir)
        if source in ("fa", "e621"):
            entries = [e for e in entries if e["source"] == source]
        total = len(entries)
        start = (page - 1) * PAGE_SIZE
        self._json(
            {
                "entries": entries[start:start + PAGE_SIZE],
                "total": total,
                "page": page,
                "pages": max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE),
            }
        )

    def _api_open_folder(self):
        cfg = load_config()
        target = self._body().get("what", "output")
        path = Path(cfg["output_dir"]) if target == "output" else APPDATA
        if not path.exists():
            self._json({"ok": False, "problems": [f"Does not exist: {path}"]}, 400)
            return
        try:
            os.startfile(path)  # noqa: S606 - opening a folder in Explorer
        except Exception as e:
            self._json({"ok": False, "problems": [str(e)]}, 500)
            return
        self._json({"ok": True})

    # -- pairing --

    def _api_pair(self):
        token, error = pairing.redeem(
            self._body().get("code", ""), self.headers.get("User-Agent", "")
        )
        if not token:
            self._json({"ok": False, "error": error}, 403)
            return
        self._json({"ok": True}, set_cookie=token)

    def _api_pair_new(self):
        if not APP.lan_enabled:
            self._json(
                {"ok": False, "problems": ["Enable network access first."]}, 400
            )
            return
        code = pairing.new_code()
        target = APP.pair_target()
        net = APP.network()
        self._json(
            {
                "ok": True,
                "code": code,
                "ttl": pairing.CODE_TTL,
                # The QR carries the numeric address; the friendly name is for
                # humans, and falls back to numeric when the hub is not up.
                "qr_url": f"{target}?pair={code}" if target else None,
                "friendly_url": net.get("hub_url") or target,
            }
        )

    def _api_pair_qr(self):
        code = pairing.current_code()
        target = APP.pair_target()
        if not code or not target:
            self.send_error(404, "No pairing in progress")
            return
        svg = _qr_svg(f"{target}?pair={code}")
        if svg is None:
            self.send_error(503, "QR generation unavailable")
            return
        self.send_response(200)
        self.send_header("Content-Type", "image/svg+xml")
        self.send_header("Content-Length", str(len(svg)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(svg)


# ---------- network plumbing ----------

def _qr_svg(data):
    """A QR as SVG text, or None when the qrcode package is missing (the UI
    then shows the URL and code as text, which still works)."""
    try:
        import qrcode
        import qrcode.image.svg
    except ImportError:
        return None
    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue()


def _fix_firewall_async():
    """Elevated rule add in the background: the UAC prompt can sit unanswered
    for minutes and must not hang a request thread. The UI watches
    state.network.firewall to report how it went."""
    APP.firewall = "pending"

    def worker():
        APP.firewall = netutil.add_rules()

    threading.Thread(target=worker, daemon=True).start()


def _apply_network_settings(cfg):
    """Called after a successful save. Network changes take effect without a
    relaunch: the serve loop in main() rebinds when asked to."""
    lan = bool(cfg.get("lan_enabled"))
    name = str(cfg.get("hub_name") or "tsidia").strip().lower()
    port = int(cfg.get("ui_port") or DEFAULT_PORT)

    # One UAC prompt at the moment the user turns LAN access on; never nag on
    # unrelated saves. The settings screen has a button for retries.
    if lan and not APP.lan_enabled:
        _fix_firewall_async()

    if lan != APP.lan_enabled or port != APP.wanted_port or name != APP.hub_name:
        APP.rebind = {"lan": lan, "port": port, "name": name}

        def trigger():
            time.sleep(0.5)  # let the save response reach the browser first
            httpd = APP.httpd
            if httpd:
                httpd.shutdown()

        threading.Thread(target=trigger, daemon=True).start()


# ---------- lifecycle ----------

def _shutdown():
    APP._shutdown.set()
    if APP.hub:
        APP.hub.stop()
    if APP.httpd:
        APP.httpd.shutdown()


def _heartbeat_watchdog():
    """Without keep-alive the open tab is the app window: when it goes, so do
    we, rather than leaving an invisible process behind. When the interface is
    meant to stay reachable, this does not run at all."""
    while not APP._shutdown.wait(10):
        if time.time() - APP.last_beat > HEARTBEAT_GRACE:
            _shutdown()
            return


class _Server(ThreadingHTTPServer):
    # Default is 1, which on Windows lets a second process bind a port another
    # process already holds instead of failing. That would silently produce two
    # servers fighting over the same port, so insist on exclusive use.
    allow_reuse_address = False


def _ours(port):
    """True if the thing already listening on `port` is another copy of us."""
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/ping?k={APP.token}", timeout=2) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "FurScraper"
    except Exception:
        return False


def _bind(port):
    # 0.0.0.0 includes loopback, so the local bookmark works in both modes.
    host = "0.0.0.0" if APP.lan_enabled else "127.0.0.1"
    try:
        return _Server((host, port), Handler)
    except OSError:
        return None


def _bind_with_fallback(wanted):
    httpd = _bind(wanted)
    if httpd is not None:
        APP.port_warning = None
        return httpd
    httpd = _bind(0)  # something else holds it; still work this session
    if httpd is not None:
        APP.port_warning = (
            f"Port {wanted} is being used by another program, so this session is "
            f"on a different one. Your bookmark will not work until that program "
            f"stops, or you set a different port below and restart."
        )
    return httpd


def _after_bind():
    """Everything that depends on the bound port: the Tsidia manifest, the hub,
    and the firewall status. Runs at startup and again after every rebind."""
    exe = str(Path(sys.executable).resolve()) if getattr(sys, "frozen", False) else ""
    try:
        tsidia_hub.write_manifest("furscraper", "FurScraper", APP.port, exe)
    except Exception:
        pass  # the hub is a convenience; the app must not die for it

    if APP.lan_enabled:
        APP.hub = tsidia_hub.Hub(APP.hub_name)
        APP.hub.start()
        if APP.firewall is None:
            # Status check only. The elevated add is reserved for the moment
            # the user enables LAN access, or for the settings button.
            APP.firewall = "pending"
            threading.Thread(
                target=lambda: setattr(APP, "firewall", netutil.rule_status()),
                daemon=True,
            ).start()
    else:
        APP.firewall = None


def main(open_browser=True):
    cfg = load_config()
    wanted = int(cfg.get("ui_port") or DEFAULT_PORT)
    APP.wanted_port = wanted
    APP.keep_running = bool(cfg.get("keep_ui_running", True))
    APP.lan_enabled = bool(cfg.get("lan_enabled", False))
    APP.hub_name = str(cfg.get("hub_name") or "tsidia").strip().lower()

    # Ask before binding: on a refused connection this returns immediately, and
    # it avoids ever racing another instance for the socket.
    if _ours(wanted):
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{wanted}/?k={APP.token}")
        return

    httpd = _bind_with_fallback(wanted)
    if httpd is None:
        if open_browser:
            dialogs_error(
                "FurScraper could not start",
                "No local port could be opened for the interface.",
            )
        return

    APP.httpd = httpd
    APP.port = httpd.server_address[1]
    APP.last_beat = time.time()
    _after_bind()

    # Only tie our lifetime to the tab when the interface is not meant to stay
    # up; otherwise closing the tab would take the bookmark down with it.
    if not APP.keep_running:
        threading.Thread(target=_heartbeat_watchdog, daemon=True).start()

    if open_browser:
        webbrowser.open(APP.url())

    # Saving a network change asks for a rebind by setting APP.rebind and
    # shutting the server down; anything else that stops it ends the process.
    while True:
        APP.httpd.serve_forever()
        request, APP.rebind = APP.rebind, None
        if APP._shutdown.is_set() or not request:
            break
        APP.lan_enabled = request["lan"]
        APP.hub_name = request["name"]
        APP.wanted_port = request["port"]
        if APP.hub:
            APP.hub.stop()
            APP.hub = None
        if not APP.lan_enabled:
            APP.firewall = None
        APP.httpd = None
        httpd = _bind_with_fallback(request["port"])
        if httpd is None:
            break  # nowhere left to serve from; exit like a failed start
        APP.httpd = httpd
        APP.port = httpd.server_address[1]
        _after_bind()


def dialogs_error(title, message):
    import dialogs

    dialogs.error(title, message)

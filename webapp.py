"""Local web UI: configuration, run control and gallery in one browser tab.

Runs a loopback HTTP server and opens the browser at it. The page is the app
window: it heartbeats while open, and the process exits shortly after the tab
closes, so nothing is left running in the background.

The address is deliberately stable (fixed port, persistent key) so it can be
bookmarked and reopened like any other page, rather than the app having to
install shortcuts to make itself findable.

Security notes, because this server can read and write credentials:

  * Bound to 127.0.0.1. Never reachable off the machine.
  * Every request must carry the access key, which lives in %APPDATA% and is
    passed in the URL. Without it any website you happen to have open could
    POST to this port; CORS does not stop a request being *sent*, only the
    reading of its response.
  * Host/Origin are validated to blunt DNS-rebinding, where a hostile domain
    re-resolves to 127.0.0.1 to talk to local services.

The key persisting on disk is a deliberate trade for the stable URL: anything
able to read it can already read config.json, which holds the actual site
credentials.
"""
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote
from urllib.request import urlopen

import gallery
import scheduler
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
        self._shutdown = threading.Event()

    def url(self):
        return f"http://127.0.0.1:{self.port}/?k={self.token}"


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
    return problems


# ---------- HTTP ----------

class Handler(BaseHTTPRequestHandler):
    server_version = "FurScraper"

    def log_message(self, fmt, *args):
        pass  # no console to log to

    # -- helpers --

    def _json(self, obj, status=200):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
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
        """Reject anything not addressed to loopback by address, and any
        cross-origin caller. Blunts DNS rebinding."""
        host = (self.headers.get("Host") or "").split(":")[0]
        if host not in ("127.0.0.1", "localhost", "::1", "[::1]"):
            return False
        origin = self.headers.get("Origin")
        if origin:
            try:
                oh = urlparse(origin).hostname
            except ValueError:
                return False
            if oh not in ("127.0.0.1", "localhost", "::1"):
                return False
        return True

    def _token_ok(self, query):
        supplied = (
            self.headers.get("X-FurScraper-Token")
            or query.get("k", [""])[0]
            or query.get("token", [""])[0]  # older bookmarks
        )
        return secrets.compare_digest(supplied or "", APP.token)

    # -- routing --

    def do_GET(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        path = parsed.path

        if not self._host_ok():
            self.send_error(403, "Forbidden")
            return

        # Stylesheet and script are requested by the browser itself, which
        # cannot attach a token, and they contain nothing worth protecting.
        # Everything that touches config, media or credentials is guarded below.
        if path.startswith("/static/"):
            self._serve_static(path[len("/static/"):])
            return

        if path == "/":
            if not self._token_ok(query):
                self.send_error(403, "Missing or invalid token")
                return
            self._serve_index()
            return

        if not self._token_ok(query):
            self.send_error(403, "Missing or invalid token")
            return

        if path == "/api/ping":
            # Used by a second launch to tell "our server" from "some other
            # program squatting on the port".
            self._json({"app": "FurScraper"})
        elif path == "/api/state":
            self._api_state()
        elif path == "/api/log":
            self._api_log(query)
        elif path == "/api/gallery":
            self._api_gallery(query)
        elif path.startswith("/media/"):
            gallery.serve_file(self, unquote(path[len("/media/"):]))
        else:
            self.send_error(404, "Not found")

    def do_POST(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        if not self._host_ok() or not self._token_ok(query):
            self.send_error(403, "Forbidden")
            return

        path = parsed.path
        if path == "/api/heartbeat":
            APP.last_beat = time.time()
            self._json({"ok": True})
        elif path == "/api/config":
            self._api_save_config()
        elif path == "/api/run":
            self._api_run()
        elif path == "/api/open-folder":
            self._api_open_folder()
        elif path == "/api/quit":
            self._json({"ok": True})
            threading.Thread(target=_shutdown, daemon=True).start()
        else:
            self.send_error(404, "Not found")

    # -- handlers --

    def _serve_index(self):
        html = (_resource_dir() / "index.html").read_text(encoding="utf-8")
        # Placeholder is distinct from the JS global's name (__TOKEN__): replace
        # is global, so a shared spelling would rewrite the identifier too.
        html = html.replace("__TOKEN_VALUE__", APP.token)
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Referrer-Policy", "no-referrer")
        self.end_headers()
        self.wfile.write(body)

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
        save_config(cfg)
        problems = []
        try:
            scheduler.register(cfg["interval_minutes"])
            scheduled = True
        except Exception as e:
            scheduled = False
            problems.append(f"Saved, but scheduling failed: {e}")
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


# ---------- lifecycle ----------

def _shutdown():
    APP._shutdown.set()
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
    try:
        return _Server(("127.0.0.1", port), Handler)
    except OSError:
        return None


def main(open_browser=True):
    cfg = load_config()
    wanted = int(cfg.get("ui_port") or DEFAULT_PORT)
    APP.wanted_port = wanted
    APP.keep_running = bool(cfg.get("keep_ui_running", True))

    # Ask before binding: on a refused connection this returns immediately, and
    # it avoids ever racing another instance for the socket.
    if _ours(wanted):
        if open_browser:
            webbrowser.open(f"http://127.0.0.1:{wanted}/?k={APP.token}")
        return

    httpd = _bind(wanted)
    if httpd is None:
        httpd = _bind(0)  # something else holds it; still work this session
        if httpd is None:
            if open_browser:
                dialogs_error(
                    "FurScraper could not start",
                    "No local port could be opened for the interface.",
                )
            return
        APP.port_warning = (
            f"Port {wanted} is being used by another program, so this session is "
            f"on a different one. Your bookmark will not work until that program "
            f"stops, or you set a different port below and restart."
        )

    APP.httpd = httpd
    APP.port = httpd.server_address[1]
    APP.last_beat = time.time()

    # Only tie our lifetime to the tab when the interface is not meant to stay
    # up; otherwise closing the tab would take the bookmark down with it.
    if not APP.keep_running:
        threading.Thread(target=_heartbeat_watchdog, daemon=True).start()

    if open_browser:
        webbrowser.open(APP.url())
    APP.httpd.serve_forever()


def dialogs_error(title, message):
    import dialogs

    dialogs.error(title, message)

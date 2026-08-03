"""The Tsidia hub: one friendly address for every Tsidia app on this machine.

Any Tsidia app can host the hub; the first one up claims port 80 (8080 as a
fallback), advertises `<name>.local` over mDNS, serves a home screen listing
the installed apps, and reverse-proxies `/<app>/...` to that app's own local
port. If the hub holder exits, any other running Tsidia app claims the port on
its next retry, so leadership follows whoever is alive and no separate hub
process ever needs installing.

Apps announce themselves by dropping a manifest into %APPDATA%/Tsidia/apps/.
The hub routes from those manifests, which also lets it list apps that are
installed but not currently running. This module is deliberately
self-contained (stdlib, plus zeroconf if available) so other projects can
vendor it unchanged.
"""
import json
import os
import re
import socket
import shutil
import threading
import time
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import urlopen

import netutil

TSIDIA_DIR = Path(os.environ.get("APPDATA", Path.home())) / "Tsidia"
APPS_DIR = TSIDIA_DIR / "apps"

HUB_PORTS = (80, 8080)
RETRY_SECONDS = 20
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,31}$")
# Hop-by-hop headers never cross a proxy; Date and Server because the handler
# emits its own and duplicates confuse caches.
_SKIP_HEADERS = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailer", "trailers", "transfer-encoding", "upgrade",
    "date", "server",
}


# ---------- app manifests ----------

def write_manifest(app_id, name, port, exe=""):
    """Called by each app at startup, so the hub always routes to the port the
    app actually bound this session."""
    APPS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "id": app_id,
        "name": name,
        "path": app_id,
        "port": int(port),
        "exe": exe,
        "updated": time.time(),
    }
    (APPS_DIR / f"{app_id}.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )


def read_manifests():
    """Path segment -> app dict. Anything malformed is skipped, not fatal: a
    bad manifest from one app must not take the hub down."""
    apps = {}
    try:
        files = sorted(APPS_DIR.glob("*.json"))
    except OSError:
        return apps
    for f in files:
        try:
            m = json.loads(f.read_text(encoding="utf-8"))
            path = str(m.get("path") or m.get("id") or "").lower()
            port = int(m.get("port"))
        except Exception:
            continue
        if not _ID_RE.match(path) or path == "tsidia" or not 0 < port < 65536:
            continue
        apps[path] = {
            "path": path,
            "name": str(m.get("name") or path),
            "port": port,
            "exe": str(m.get("exe") or ""),
        }
    return apps


def _app_alive(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/api/ping", timeout=1) as r:
            return bool(json.loads(r.read().decode("utf-8")).get("app"))
    except Exception:
        return False


def _hub_alive(port):
    try:
        with urlopen(f"http://127.0.0.1:{port}/tsidia/api/ping", timeout=1) as r:
            return json.loads(r.read().decode("utf-8")).get("app") == "TsidiaHub"
    except Exception:
        return False


# ---------- mDNS ----------

class _Mdns:
    """Advertises <name>.local pointing at this machine. Re-registers when the
    LAN address changes, since DHCP will eventually move a long-lived host."""

    def __init__(self, name, port):
        self.name = name
        self.port = port
        self.zc = None
        self.info = None
        self._ips = []
        self._stop = threading.Event()

    def start(self):
        try:
            from zeroconf import Zeroconf
        except ImportError:
            return False
        try:
            self.zc = Zeroconf()
            self._register()
            threading.Thread(
                target=self._watch, daemon=True, name="tsidia-mdns"
            ).start()
            return True
        except Exception:
            self.stop()
            return False

    def _register(self):
        from zeroconf import ServiceInfo

        ips = netutil.lan_ips()
        self._ips = ips
        self.info = ServiceInfo(
            "_http._tcp.local.",
            f"Tsidia ({self.name})._http._tcp.local.",
            addresses=[socket.inet_aton(ip) for ip in ips] or None,
            port=self.port,
            server=f"{self.name}.local.",
        )
        self.zc.register_service(self.info)

    def _watch(self):
        while not self._stop.wait(60):
            try:
                if netutil.lan_ips(max_age=0) != self._ips:
                    self.zc.unregister_service(self.info)
                    self._register()
            except Exception:
                pass

    def stop(self):
        self._stop.set()
        try:
            if self.zc:
                if self.info:
                    self.zc.unregister_service(self.info)
                self.zc.close()
        except Exception:
            pass


# ---------- the hub itself ----------

class Hub:
    """Claims the hub role when it is vacant, defers when another app holds it,
    and keeps retrying either way. `status()` is safe from any thread."""

    def __init__(self, name):
        self.name = name
        self.role = "starting"    # starting | leader | client | unavailable
        self.port = None
        self.warning = None
        self.mdns_ok = None
        self._stop = threading.Event()
        self._httpd = None

    def status(self):
        return {
            "role": self.role,
            "port": self.port,
            "name": self.name,
            "warning": self.warning,
            "mdns": self.mdns_ok,
        }

    def start(self):
        threading.Thread(target=self._run, daemon=True, name="tsidia-hub").start()

    def stop(self):
        self._stop.set()
        httpd = self._httpd
        if httpd:
            try:
                httpd.shutdown()
            except Exception:
                pass

    def _run(self):
        while not self._stop.is_set():
            claimed = self._claim()
            if claimed:
                self._httpd = claimed
                self.role = "leader"
                self.port = claimed.server_address[1]
                self.warning = None if self.port == HUB_PORTS[0] else (
                    f"Port {HUB_PORTS[0]} is taken by another program, so the "
                    "Tsidia address needs an explicit port: "
                    f"{self.name}.local:{self.port}."
                )
                mdns = _Mdns(self.name, self.port)
                self.mdns_ok = mdns.start()
                try:
                    claimed.serve_forever()
                finally:
                    mdns.stop()
                    self._httpd = None
                if self._stop.is_set():
                    return
            self._stop.wait(RETRY_SECONDS)

    def _claim(self):
        """Try each hub port: an existing hub means we are a client; a free
        port means we lead; anything else squatting means try the next."""
        for port in HUB_PORTS:
            if _hub_alive(port):
                self.role, self.port, self.warning = "client", port, None
                return None
            try:
                return _HubServer(("0.0.0.0", port), _HubHandler, hub=self)
            except OSError:
                continue
        self.role, self.port = "unavailable", None
        self.warning = (
            "Ports 80 and 8080 are both in use by other programs, so the "
            "Tsidia address is unavailable. Direct addresses still work."
        )
        return None


class _HubServer(ThreadingHTTPServer):
    # Windows lets a second process bind an already-held port unless reuse is
    # off; two hubs fighting over 80 would be worse than no hub.
    allow_reuse_address = False
    daemon_threads = True

    def __init__(self, addr, handler, hub):
        self.hub = hub
        super().__init__(addr, handler)


class _HubHandler(BaseHTTPRequestHandler):
    server_version = "TsidiaHub"
    protocol_version = "HTTP/1.1"
    timeout = 60

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        self._route()

    def do_HEAD(self):
        self._route()

    def do_POST(self):
        self._route()

    # -- responses --

    def _send(self, status, body, ctype="text/html; charset=utf-8", extra=()):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in extra:
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status=200):
        self._send(status, json.dumps(obj).encode("utf-8"),
                   "application/json; charset=utf-8", [("Cache-Control", "no-store")])

    # -- routing --

    def _route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/":
            self._send(200, _home_html(self.server.hub.name),
                       extra=[("Cache-Control", "no-store")])
            return
        if path == "/tsidia/api/ping":
            self._json({"app": "TsidiaHub", "name": self.server.hub.name})
            return
        if path == "/tsidia/api/apps":
            self._apps()
            return

        segment, _, rest = path.lstrip("/").partition("/")
        app = read_manifests().get(segment.lower())
        if not app:
            self.send_error(404, "Not found")
            return
        if path == f"/{segment}":
            # Land apps on a trailing slash so their relative URLs resolve
            # under the prefix. 308 keeps the method for the odd POST.
            self._send(308, b"", extra=[("Location", f"/{segment}/")])
            return
        if app["port"] == self.server.server_address[1]:
            self.send_error(508, "Manifest routes to the hub itself")
            return
        target = "/" + rest + (f"?{parsed.query}" if parsed.query else "")
        self._proxy(app, target)

    def _apps(self):
        apps = [
            {
                "id": a["path"],
                "name": a["name"],
                "href": f"/{a['path']}/",
                "running": _app_alive(a["port"]),
            }
            for a in read_manifests().values()
        ]
        self._json({"name": self.server.hub.name, "apps": apps})

    # -- the proxy --

    def _proxy(self, app, target):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        # Client-supplied Forwarded headers are dropped, not passed through:
        # apps treat our X-Forwarded-For as the true client address (it is how
        # they tell the host machine from a remote device), so a forgeable
        # copy must never reach them.
        headers = {
            k: v for k, v in self.headers.items()
            if k.lower() not in _SKIP_HEADERS
            and k.lower() != "forwarded"
            and not k.lower().startswith("x-forwarded-")
        }
        # The original Host goes through untouched: the app validates it
        # against its allowlist, exactly as if there were no proxy.
        headers["Connection"] = "close"
        headers["X-Forwarded-For"] = self.client_address[0]
        headers["X-Forwarded-Prefix"] = f"/{app['path']}"
        conn = HTTPConnection("127.0.0.1", app["port"], timeout=30)
        try:
            conn.request(self.command, target, body=body, headers=headers)
            resp = conn.getresponse()
        except OSError:
            conn.close()
            self._send(502, _NOT_RUNNING_HTML.format(name=app["name"]).encode("utf-8"))
            return
        try:
            has_length = resp.getheader("Content-Length") is not None
            buffered = None if has_length else resp.read()
            self.send_response(resp.status)
            for k, v in resp.getheaders():
                if k.lower() not in _SKIP_HEADERS and k.lower() != "content-length":
                    self.send_header(k, v)
            self.send_header(
                "Content-Length",
                resp.getheader("Content-Length") if has_length else str(len(buffered)),
            )
            self.end_headers()
            if self.command != "HEAD":
                if has_length:
                    shutil.copyfileobj(resp, self.wfile, 64 * 1024)
                else:
                    self.wfile.write(buffered)
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            conn.close()


_NOT_RUNNING_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{name} is not running</title>
<style>body{{background:#0e1116;color:#e6edf3;font:15px/1.6 "Segoe UI",Roboto,sans-serif;
display:grid;place-items:center;min-height:100vh;margin:0}}main{{text-align:center}}
p{{color:#8b98a9}}a{{color:#6c8cff}}</style></head>
<body><main><h1>{name} is not running</h1>
<p>Start it on the computer it is installed on, then reload this page.</p>
<p><a href="/">Back to the hub</a></p></main></body></html>
"""


def _home_html(name):
    title = name[:1].upper() + name[1:]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{title}</title>
<style>
:root{{--bg:#0e1116;--raised:#161b22;--line:#232a35;--text:#e6edf3;--dim:#8b98a9;
--faint:#5f6b7a;--accent:#6c8cff;--good:#3fb950}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--text);
font:15px/1.6 -apple-system,"Segoe UI",Inter,Roboto,sans-serif;min-height:100vh;
display:flex;flex-direction:column;align-items:center;padding:48px 20px}}
h1{{margin:0;font-size:34px;letter-spacing:-0.02em}}
.sub{{color:var(--dim);margin:6px 0 34px}}
.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));
gap:14px;width:min(760px,100%)}}
a.app{{display:flex;flex-direction:column;gap:6px;background:var(--raised);
border:1px solid var(--line);border-radius:12px;padding:18px;
text-decoration:none;color:inherit;transition:transform .12s,border-color .12s}}
a.app:hover{{transform:translateY(-2px);border-color:#33405a}}
a.app h2{{margin:0;font-size:17px}}
.state{{font-size:12.5px;color:var(--faint);display:flex;gap:6px;align-items:center}}
.dot{{width:7px;height:7px;border-radius:50%;background:var(--faint)}}
.dot.on{{background:var(--good)}}
.empty{{color:var(--faint)}}
footer{{margin-top:auto;padding-top:40px;color:var(--faint);font-size:12.5px}}
</style></head>
<body>
<h1>{title}</h1>
<p class="sub">Apps on this computer</p>
<div class="grid" id="grid"><p class="empty">Loading...</p></div>
<footer>Tsidia hub</footer>
<script>
fetch("tsidia/api/apps").then(r => r.json()).then(d => {{
  const grid = document.getElementById("grid");
  grid.innerHTML = "";
  if (!d.apps.length) {{
    grid.innerHTML = '<p class="empty">No Tsidia apps found on this computer.</p>';
    return;
  }}
  for (const a of d.apps) {{
    const el = document.createElement("a");
    el.className = "app";
    el.href = a.href;
    el.innerHTML = `<h2></h2><span class="state"><span class="dot"></span></span>`;
    el.querySelector("h2").textContent = a.name;
    el.querySelector(".state").append(a.running ? "running" : "not running");
    el.querySelector(".dot").classList.toggle("on", a.running);
    grid.appendChild(el);
  }}
}}).catch(() => {{}});
</script>
</body></html>""".encode("utf-8")

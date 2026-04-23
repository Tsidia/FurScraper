"""Local HTTP gallery for FurScraper output.

Launches a tiny HTTP server on 127.0.0.1 that serves a combined e621/FA
browsing grid styled after e621. Files in the output directory are
classified by filename: `fa_<id>.<ext>` is FA, `<digits>.<ext>` is e621.
"""

import json
import re
import threading
import webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs, quote, unquote

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "apng", "bmp"}
VIDEO_EXTS = {"mp4", "webm", "mov", "m4v", "ogv"}

PAGE_SIZE = 120

E6_RE = re.compile(r"^(\d+)\.([a-z0-9]+)$", re.I)
FA_RE = re.compile(r"^fa_(\d+)\.([a-z0-9]+)$", re.I)

_state_lock = threading.Lock()
_server_state = {"httpd": None, "thread": None, "port": None, "out_dir": None}


def _classify(name):
    m = FA_RE.match(name)
    if m:
        return "fa", m.group(1), m.group(2).lower()
    m = E6_RE.match(name)
    if m:
        return "e621", m.group(1), m.group(2).lower()
    return None


def _list_entries(out_dir):
    entries = []
    try:
        names = list(out_dir.iterdir())
    except FileNotFoundError:
        return entries
    for p in names:
        if not p.is_file():
            continue
        cls = _classify(p.name)
        if not cls:
            continue
        source, pid, ext = cls
        if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
            continue
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        entries.append({
            "name": p.name,
            "source": source,
            "id": pid,
            "ext": ext,
            "is_video": ext in VIDEO_EXTS,
            "mtime": mtime,
        })
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "apng": "image/apng",
    "bmp": "image/bmp",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "m4v": "video/x-m4v", "ogv": "video/ogg",
}


PAGE_TEMPLATE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>FurScraper Gallery</title>
<style>
  * { box-sizing: border-box; }
  html, body { margin: 0; padding: 0; }
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif;
         background: #152f56; color: #d7e4f5; min-height: 100vh; }
  header { background: #1c3d6e; padding: 10px 16px; display: flex; align-items: center;
           gap: 16px; flex-wrap: wrap; border-bottom: 1px solid #0b1a33;
           position: sticky; top: 0; z-index: 10; }
  header h1 { margin: 0; font-size: 18px; font-weight: 600; color: #fff; }
  header .count { color: #8fa6c7; font-size: 13px; margin-left: auto; }
  nav a { color: #d7e4f5; text-decoration: none; margin-right: 6px; padding: 5px 11px;
          border-radius: 3px; font-size: 13px; }
  nav a.active { background: #2e588d; color: #fff; }
  nav a:hover { background: #2e588d; color: #fff; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
          gap: 4px; padding: 4px; }
  .tile { position: relative; background: #0b1a33; overflow: hidden; aspect-ratio: 1;
          border-radius: 3px; cursor: pointer; }
  .tile img, .tile video { width: 100%; height: 100%; object-fit: cover; display: block;
                           background: #0b1a33; }
  .tile .badge { position: absolute; top: 4px; left: 4px; color: #fff; padding: 2px 6px;
                 font-size: 10px; border-radius: 2px; text-transform: uppercase;
                 letter-spacing: 0.5px; font-weight: 600; }
  .tile .badge.fa { background: rgba(197,141,0,0.9); }
  .tile .badge.e621 { background: rgba(1,43,83,0.95); }
  .tile .vidflag { position: absolute; top: 4px; right: 4px; background: rgba(0,0,0,0.75);
                   color: #fff; padding: 2px 6px; font-size: 10px; border-radius: 2px;
                   letter-spacing: 0.5px; font-weight: 600; }
  .pager { padding: 16px; text-align: center; }
  .pager a, .pager span { color: #d7e4f5; padding: 6px 12px; margin: 0 2px;
                          background: #1c3d6e; border-radius: 3px; text-decoration: none;
                          display: inline-block; font-size: 13px; }
  .pager span.curr { background: #2e588d; color: #fff; }
  .pager a:hover { background: #2e588d; color: #fff; }
  .empty { padding: 64px 16px; text-align: center; color: #8fa6c7; }
  .lb { position: fixed; inset: 0; background: rgba(0,0,0,0.93); display: none;
        align-items: center; justify-content: center; z-index: 100; }
  .lb.open { display: flex; }
  .lb img, .lb video { max-width: 95vw; max-height: 90vh; display: block; }
  .lb .close { position: absolute; top: 10px; right: 14px; color: #fff; font-size: 32px;
               line-height: 1; cursor: pointer; background: none; border: none;
               padding: 4px 10px; }
  .lb .nav-btn { position: absolute; top: 50%; transform: translateY(-50%);
                 background: rgba(0,0,0,0.5); color: #fff; border: none; cursor: pointer;
                 font-size: 42px; padding: 8px 18px; border-radius: 4px; }
  .lb .nav-btn:hover { background: rgba(0,0,0,0.8); }
  .lb .prev { left: 12px; }
  .lb .next { right: 12px; }
  .lb .info { position: absolute; bottom: 14px; left: 14px; color: #d7e4f5;
              background: rgba(0,0,0,0.7); padding: 6px 12px; border-radius: 3px;
              font-size: 13px; }
  .lb .info a { color: #7fb3ff; text-decoration: none; }
  .lb .info a:hover { text-decoration: underline; }
</style>
</head>
<body>
<header>
  <h1>FurScraper Gallery</h1>
  <nav>__NAV__</nav>
  <span class="count">__COUNT__</span>
</header>
__BODY__
<div class="lb" id="lb">
  <button class="close" onclick="closeLb()" title="Close (Esc)">&times;</button>
  <button class="nav-btn prev" onclick="step(-1)" title="Previous (&larr;)">&#10094;</button>
  <button class="nav-btn next" onclick="step(1)" title="Next (&rarr;)">&#10095;</button>
  <div id="lb-content"></div>
  <div class="info" id="lb-info"></div>
</div>
<script>
const entries = __ENTRIES_JSON__;
let lbIndex = -1;
function openLb(i){
  lbIndex = i;
  const e = entries[i];
  const c = document.getElementById('lb-content');
  c.innerHTML = '';
  let el;
  if (e.is_video){
    el = document.createElement('video');
    el.src = '/file/' + encodeURIComponent(e.name);
    el.controls = true; el.autoplay = true; el.loop = true;
  } else {
    el = document.createElement('img');
    el.src = '/file/' + encodeURIComponent(e.name);
  }
  c.appendChild(el);
  const src = e.source === 'fa'
    ? 'https://www.furaffinity.net/view/' + e.id + '/'
    : 'https://e621.net/posts/' + e.id;
  document.getElementById('lb-info').innerHTML =
    e.source.toUpperCase() + ' #' + e.id +
    ' &middot; <a href="' + src + '" target="_blank" rel="noopener">open on site</a>';
  document.getElementById('lb').classList.add('open');
}
function closeLb(){
  document.getElementById('lb').classList.remove('open');
  document.getElementById('lb-content').innerHTML = '';
  lbIndex = -1;
}
function step(d){
  if (lbIndex < 0) return;
  const n = lbIndex + d;
  if (n < 0 || n >= entries.length) return;
  openLb(n);
}
document.addEventListener('keydown', (ev) => {
  if (!document.getElementById('lb').classList.contains('open')) return;
  if (ev.key === 'Escape') closeLb();
  else if (ev.key === 'ArrowLeft') step(-1);
  else if (ev.key === 'ArrowRight') step(1);
});
document.querySelectorAll('.tile').forEach((t, i) => {
  t.addEventListener('click', () => openLb(i));
});
</script>
</body>
</html>
"""


def _render_page(out_dir, source_filter, page):
    all_entries = _list_entries(out_dir)
    counts = {
        "all": len(all_entries),
        "e621": sum(1 for e in all_entries if e["source"] == "e621"),
        "fa": sum(1 for e in all_entries if e["source"] == "fa"),
    }
    if source_filter in ("e621", "fa"):
        filtered = [e for e in all_entries if e["source"] == source_filter]
    else:
        source_filter = "all"
        filtered = all_entries

    total = len(filtered)
    pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
    page = max(1, min(page, pages))
    start = (page - 1) * PAGE_SIZE
    chunk = filtered[start:start + PAGE_SIZE]

    tiles = []
    for e in chunk:
        url = f"/file/{quote(e['name'])}"
        if e["is_video"]:
            thumb = f'<video muted preload="metadata" src="{url}#t=0.1"></video>'
            vid = '<span class="vidflag">VIDEO</span>'
        else:
            thumb = f'<img loading="lazy" src="{url}" alt="">'
            vid = ""
        tiles.append(
            f'<div class="tile">'
            f'<span class="badge {e["source"]}">{e["source"]}</span>'
            f'{vid}{thumb}</div>'
        )
    if tiles:
        grid = f'<div class="grid">{"".join(tiles)}</div>'
    else:
        grid = '<div class="empty">No files yet. Run the scraper to fill your gallery.</div>'

    pager_html = ""
    if pages > 1:
        bits = []
        if page > 1:
            bits.append(f'<a href="?src={source_filter}&page={page-1}">&lsaquo; Prev</a>')
        bits.append(f'<span class="curr">Page {page} of {pages}</span>')
        if page < pages:
            bits.append(f'<a href="?src={source_filter}&page={page+1}">Next &rsaquo;</a>')
        pager_html = '<div class="pager">' + "".join(bits) + "</div>"

    def nav_link(key, label, n):
        cls = "active" if source_filter == key else ""
        return f'<a class="{cls}" href="?src={key}&page=1">{label} ({n})</a>'

    nav = (
        nav_link("all", "All", counts["all"])
        + nav_link("e621", "e621", counts["e621"])
        + nav_link("fa", "FurAffinity", counts["fa"])
    )
    count_label = f"Showing {len(chunk)} of {total}"

    return (
        PAGE_TEMPLATE
        .replace("__NAV__", nav)
        .replace("__COUNT__", count_label)
        .replace("__BODY__", grid + pager_html)
        .replace("__ENTRIES_JSON__", json.dumps(chunk))
    )


class _Handler(BaseHTTPRequestHandler):
    out_dir = None  # overridden per-server

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path in ("/", "/index.html"):
            self._serve_index(parsed.query)
            return
        if parsed.path.startswith("/file/"):
            self._serve_file(unquote(parsed.path[len("/file/"):]))
            return
        self.send_error(404)

    def _serve_index(self, query):
        qs = parse_qs(query)
        src = qs.get("src", ["all"])[0]
        try:
            page = int(qs.get("page", ["1"])[0])
        except ValueError:
            page = 1
        try:
            html = _render_page(self.out_dir, src, page).encode("utf-8")
        except Exception as e:
            body = f"<h1>Gallery error</h1><pre>{e}</pre>".encode("utf-8")
            self.send_response(500)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html)

    def _serve_file(self, name):
        safe = Path(name).name
        if not safe or safe != name:
            self.send_error(400)
            return
        path = self.out_dir / safe
        if not path.is_file():
            self.send_error(404)
            return
        try:
            size = path.stat().st_size
        except OSError:
            self.send_error(500)
            return
        ext = path.suffix.lower().lstrip(".")
        ctype = _MIME.get(ext, "application/octet-stream")

        start, end = 0, size - 1
        status = 200
        range_hdr = self.headers.get("Range")
        if range_hdr and range_hdr.startswith("bytes="):
            try:
                s, _, e = range_hdr[6:].partition("-")
                if s:
                    start = int(s)
                if e:
                    end = int(e)
                end = min(end, size - 1)
                if start < 0 or start >= size or start > end:
                    self.send_response(416)
                    self.send_header("Content-Range", f"bytes */{size}")
                    self.end_headers()
                    return
                status = 206
            except ValueError:
                start, end, status = 0, size - 1, 200

        length = end - start + 1
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(length))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        try:
            with open(path, "rb") as f:
                f.seek(start)
                remaining = length
                while remaining > 0:
                    chunk = f.read(min(64 * 1024, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass


def start(out_dir):
    """Start the gallery server (or reuse one already bound to out_dir). Returns the URL."""
    out_dir = Path(out_dir)
    with _state_lock:
        if _server_state["httpd"] is not None and _server_state["out_dir"] == out_dir:
            return f"http://127.0.0.1:{_server_state['port']}/"
        if _server_state["httpd"] is not None:
            _server_state["httpd"].shutdown()
            _server_state["httpd"].server_close()
            _server_state["httpd"] = None

        handler_cls = type("GalleryHandler", (_Handler,), {"out_dir": out_dir})
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        _server_state.update(httpd=httpd, thread=thread, port=port, out_dir=out_dir)
        return f"http://127.0.0.1:{port}/"


def open_gallery(out_dir):
    url = start(out_dir)
    webbrowser.open(url)
    return url


if __name__ == "__main__":
    import sys
    from common import load_config
    target = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(load_config()["output_dir"])
    print("Serving gallery from:", target)
    print(open_gallery(target))
    threading.Event().wait()

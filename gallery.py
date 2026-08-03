"""Gallery media library: enumerate downloaded files and serve them.

The browsing UI itself lives in the web app (see webapp.py and ui/). This module
only does two things: classify what is in the output folder, and stream a file
back with Range support so video seeking works.
"""
import re
from pathlib import Path

IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "apng", "bmp"}
VIDEO_EXTS = {"mp4", "webm", "mov", "m4v", "ogv"}

# Filenames encode their origin: fa_<id>.<ext> for FurAffinity, <digits>.<ext>
# for e621. Anything else in the folder is ignored.
E6_RE = re.compile(r"^(\d+)\.([a-z0-9]+)$", re.I)
FA_RE = re.compile(r"^fa_(\d+)\.([a-z0-9]+)$", re.I)

POST_URL = {
    "fa": "https://www.furaffinity.net/view/{id}/",
    "e621": "https://e621.net/posts/{id}",
}

_MIME = {
    "jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
    "gif": "image/gif", "webp": "image/webp", "apng": "image/apng",
    "bmp": "image/bmp",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "m4v": "video/x-m4v", "ogv": "video/ogg",
}


def classify(name):
    m = FA_RE.match(name)
    if m:
        return "fa", m.group(1), m.group(2).lower()
    m = E6_RE.match(name)
    if m:
        return "e621", m.group(1), m.group(2).lower()
    return None


def list_entries(out_dir):
    """Every recognised file in the output folder, newest first."""
    entries = []
    out_dir = Path(out_dir)
    try:
        names = list(out_dir.iterdir())
    except (FileNotFoundError, NotADirectoryError, OSError):
        return entries
    for p in names:
        if not p.is_file():
            continue
        cls = classify(p.name)
        if not cls:
            continue
        source, pid, ext = cls
        if ext not in IMAGE_EXTS and ext not in VIDEO_EXTS:
            continue
        try:
            stat = p.stat()
        except OSError:
            continue
        entries.append(
            {
                "name": p.name,
                "source": source,
                "id": pid,
                "ext": ext,
                "is_video": ext in VIDEO_EXTS,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "post_url": POST_URL[source].format(id=pid),
            }
        )
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    return entries


def serve_file(handler, name):
    """Stream one file to an HTTP handler, honouring Range so video seeks work."""
    from common import load_config

    safe = Path(name).name
    if not safe or safe != name or classify(safe) is None:
        handler.send_error(400)
        return
    out_dir = Path(load_config().get("output_dir", ""))
    path = out_dir / safe
    if not path.is_file():
        handler.send_error(404)
        return
    try:
        size = path.stat().st_size
    except OSError:
        handler.send_error(500)
        return
    ctype = _MIME.get(path.suffix.lower().lstrip("."), "application/octet-stream")

    start, end = 0, size - 1
    status = 200
    range_hdr = handler.headers.get("Range")
    if range_hdr and range_hdr.startswith("bytes="):
        try:
            s, _, e = range_hdr[6:].partition("-")
            if s:
                start = int(s)
            if e:
                end = int(e)
            end = min(end, size - 1)
            if start < 0 or start >= size or start > end:
                handler.send_response(416)
                handler.send_header("Content-Range", f"bytes */{size}")
                handler.end_headers()
                return
            status = 206
        except ValueError:
            start, end, status = 0, size - 1, 200

    length = end - start + 1
    handler.send_response(status)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(length))
    handler.send_header("Accept-Ranges", "bytes")
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.send_header("Cache-Control", "private, max-age=3600")
    handler.end_headers()
    try:
        with open(path, "rb") as f:
            f.seek(start)
            remaining = length
            while remaining > 0:
                chunk = f.read(min(64 * 1024, remaining))
                if not chunk:
                    break
                handler.wfile.write(chunk)
                remaining -= len(chunk)
    except (BrokenPipeError, ConnectionResetError):
        pass

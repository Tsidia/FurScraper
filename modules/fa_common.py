import time
from urllib.parse import urlparse

import requests

from .base import save_download

FAEXPORT_BASE = "https://faexport.spangle.org.uk"
FA_REQ_DELAY = 1.1
SOURCE = "fa"
UA = "FurScraper/1.0"
FIRST_RUN_MAX_PAGES = 1  # cap artist/search first-run at one page to avoid blowups


class FAClient:
    def __init__(self, cookie_a="", cookie_b=""):
        self.session = requests.Session()
        self.session.headers["User-Agent"] = UA
        if cookie_a and cookie_b:
            # faexport reads this header and forwards to FA as session cookies
            self.session.headers["FA_COOKIE"] = f"b={cookie_b}; a={cookie_a}"

    def _get(self, path, params=None):
        time.sleep(FA_REQ_DELAY)
        r = self.session.get(
            f"{FAEXPORT_BASE}{path}", params=params or {}, timeout=30
        )
        if not r.ok:
            body = (r.text or "").strip()[:400]
            raise RuntimeError(
                f"{r.status_code} {r.reason} from {path} — {body!r}"
            )
        return r.json()

    def gallery(self, username, page=1):
        return self._get(
            f"/user/{username}/gallery.json", {"page": page}
        )

    def submission(self, sid):
        return self._get(f"/submission/{sid}.json")

    def search(self, query, page=1):
        return self._get(
            "/search.json",
            {
                "q": query,
                "page": page,
                "order_by": "date",
                "order_direction": "desc",
            },
        )

    def notifications_submissions(self, frm=None):
        params = {}
        if frm:
            params["from"] = frm
        return self._get("/notifications/submissions.json", params)


def make_client(ctx):
    auth = ctx.fa_auth or {}
    return FAClient(auth.get("cookie_a", ""), auth.get("cookie_b", ""))


def _ext_from_url(url):
    path = urlparse(url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext and len(ext) <= 5 and ext.isalnum():
            return ext
    return "bin"


def process_submission(sub_id, client, ctx):
    """Fetch detail, apply blacklist, download if unseen. Returns (downloaded, error)."""
    sid = str(sub_id)
    if ctx.seen.is_seen(SOURCE, sid):
        return False, False
    try:
        detail = client.submission(sub_id)
    except Exception as e:
        ctx.logger.error(f"FA submission detail failed for {sid}: {e}")
        return False, True
    keywords = {k.lower() for k in (detail.get("keywords") or [])}
    blacklist = {t.lower() for t in ctx.blacklist}
    if keywords & blacklist:
        ctx.seen.mark_seen(SOURCE, sid)
        ctx.seen.commit()
        return False, False
    url = detail.get("download")
    if not url:
        ctx.seen.mark_seen(SOURCE, sid)
        ctx.seen.commit()
        return False, False
    ext = _ext_from_url(url)
    target = ctx.out_dir / f"fa_{sid}.{ext}"
    try:
        time.sleep(FA_REQ_DELAY)
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        saved = save_download(ctx, SOURCE, sid, r.content, target)
        ctx.seen.mark_seen(SOURCE, sid)
        ctx.seen.commit()
        return saved, False
    except Exception as e:
        ctx.logger.error(f"FA download failed for {sid}: {e}")
        return False, True

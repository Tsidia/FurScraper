import time

import requests

from .base import save_download
from .fa_site import (
    FAAccountDisabledError,
    FACloudflareError,
    FAContentFilterError,
    FAError,
    FALoginError,
    FANotFoundError,
    FANoUserError,
    FASite,
    FASlowdownError,
    FAStyleError,
    USER_AGENT,
)

FA_REQ_DELAY = 1.1
SOURCE = "fa"
UA = USER_AGENT
FIRST_RUN_MAX_PAGES = 1  # cap artist/search first-run at one page to avoid blowups

# Failures that mean every subsequent request will fail the same way. Raised out
# of the per-submission loop so a module aborts instead of hammering FA once per
# item with dead cookies or a wrong site theme.
FATAL_ERRORS = (FALoginError, FAStyleError, FACloudflareError, FASlowdownError)

# Failures that apply to one submission only: log, count, move on.
SKIPPABLE_ERRORS = (FANotFoundError, FANoUserError, FAContentFilterError, FAAccountDisabledError)


def make_client(ctx):
    auth = ctx.fa_auth or {}
    return FASite(
        auth.get("cookie_a", ""),
        auth.get("cookie_b", ""),
        logger=getattr(ctx, "logger", None),
    )


def _ext_from_url(url):
    from urllib.parse import urlparse

    path = urlparse(url).path
    if "." in path:
        ext = path.rsplit(".", 1)[-1].lower()
        if ext and len(ext) <= 5 and ext.isalnum():
            return ext
    return "bin"


def process_submission(sub_id, client, ctx):
    """Fetch detail, apply blacklist, download if unseen. Returns (downloaded, error).

    Raises on failures that would repeat for every remaining submission.
    """
    sid = str(sub_id)
    if ctx.seen.is_seen(SOURCE, sid):
        return False, False
    try:
        detail = client.submission(sub_id)
    except FATAL_ERRORS:
        raise
    except (FAError, Exception) as e:
        ctx.logger.error(f"FA submission detail failed for {sid}: {e}")
        # Nothing to retry for a deleted or filtered submission; remember it so
        # later runs do not keep asking FA about it.
        if isinstance(e, SKIPPABLE_ERRORS):
            ctx.seen.mark_seen(SOURCE, sid)
            ctx.seen.commit()
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
        # Plain request, deliberately without the session cookies: the CDN does
        # not need them and there is no reason to hand them to another host.
        r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        r.raise_for_status()
        saved = save_download(ctx, SOURCE, sid, r.content, target)
        ctx.seen.mark_seen(SOURCE, sid)
        ctx.seen.commit()
        return saved, False
    except Exception as e:
        ctx.logger.error(f"FA download failed for {sid}: {e}")
        return False, True

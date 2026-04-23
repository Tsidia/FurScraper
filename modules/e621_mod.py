import time

import requests
from requests.auth import HTTPBasicAuth

from .base import Module

API_URL = "https://e621.net/posts.json"
UA_TEMPLATE = "FurScraper/1.0 (by {username} on e621)"
REQ_DELAY = 1.1
PAGE_LIMIT = 320
FIRST_RUN_MAX_POSTS = 320
SOURCE = "e621"


class E621Module(Module):
    KEY = "e621"
    LABEL = "e621"

    def run(self, mod_cfg, ctx):
        if not mod_cfg.get("username") or not mod_cfg.get("api_key"):
            raise RuntimeError("e621: missing username or API key")
        searches = [s.strip() for s in mod_cfg.get("searches", []) if s.strip()]
        if not searches:
            ctx.logger.info("e621: no searches configured")
            return 0, 0

        headers = {"User-Agent": UA_TEMPLATE.format(username=mod_cfg["username"])}
        auth = HTTPBasicAuth(mod_cfg["username"], mod_cfg["api_key"])
        blacklist = {t.lower() for t in ctx.blacklist}

        total_new = 0
        total_errs = 0

        for query in searches:
            ctx.logger.info(f"e621 search: {query}")
            first_run = not ctx.seen.search_initialized(SOURCE, query)
            page = 1
            found = 0
            stop = False

            while not stop:
                time.sleep(REQ_DELAY)
                try:
                    r = requests.get(
                        API_URL,
                        params={"tags": query, "limit": PAGE_LIMIT, "page": page},
                        headers=headers,
                        auth=auth,
                        timeout=30,
                    )
                    r.raise_for_status()
                    posts = r.json().get("posts", [])
                except Exception as e:
                    ctx.logger.error(f"e621 API error on '{query}' page {page}: {e}")
                    raise

                if not posts:
                    break

                for post in posts:
                    pid = post["id"]
                    if ctx.seen.is_seen(SOURCE, pid):
                        stop = True  # e621 returns newest-first; older posts are seen too
                        break
                    if _has_blacklisted_tag(post, blacklist):
                        ctx.seen.mark_seen(SOURCE, pid)
                        ctx.seen.commit()
                        continue
                    file_info = post.get("file") or {}
                    url = file_info.get("url")
                    ext = file_info.get("ext", "bin")
                    if not url:
                        ctx.seen.mark_seen(SOURCE, pid)
                        ctx.seen.commit()
                        continue
                    target = ctx.out_dir / f"{pid}.{ext}"
                    try:
                        time.sleep(REQ_DELAY)
                        dl = requests.get(url, headers=headers, auth=auth, timeout=60)
                        dl.raise_for_status()
                        target.write_bytes(dl.content)
                        ctx.seen.mark_seen(SOURCE, pid)
                        ctx.seen.commit()
                        total_new += 1
                        found += 1
                    except Exception as e:
                        ctx.logger.error(f"e621 download failed for {pid}: {e}")
                        total_errs += 1

                    if first_run and found >= FIRST_RUN_MAX_POSTS:
                        stop = True
                        break

                if stop:
                    break
                page += 1

            ctx.seen.mark_search_initialized(SOURCE, query)
            ctx.seen.commit()
            ctx.logger.info(f"e621 '{query}': {found} new")

        return total_new, total_errs


def _has_blacklisted_tag(post, blacklist):
    if not blacklist:
        return False
    tags = set()
    for cat in post.get("tags", {}).values():
        tags.update(cat)
    return bool({t.lower() for t in tags} & blacklist)

from .base import Module
from .fa_common import (
    FIRST_RUN_MAX_PAGES,
    SOURCE,
    make_client,
    process_submission,
)

STATE_PREFIX = "search:"


class FASearchModule(Module):
    KEY = "fa_search"
    LABEL = "FA Search"

    def run(self, mod_cfg, ctx):
        queries = [q.strip() for q in mod_cfg.get("queries", []) if q.strip()]
        if not queries:
            ctx.logger.info("FA Search: no queries configured")
            return 0, 0

        client = make_client(ctx)
        total_new = 0
        total_errs = 0

        for query in queries:
            state_key = STATE_PREFIX + query
            first_run = not ctx.seen.search_initialized(SOURCE, state_key)
            page = 1
            found = 0
            stop = False
            ctx.logger.info(f"FA search: {query}")

            while not stop:
                try:
                    items = client.search(query, page=page)  # date-desc, newest first
                except Exception as e:
                    ctx.logger.error(
                        f"FA search failed for '{query}' page {page}: {e}"
                    )
                    total_errs += 1
                    break
                if not items:
                    break
                for item in items:
                    sid = item.get("id") if isinstance(item, dict) else item
                    if not sid:
                        continue
                    if ctx.seen.is_seen(SOURCE, sid):
                        stop = True  # forced date-desc ordering; older are seen too
                        break
                    dl, err = process_submission(sid, client, ctx)
                    if dl:
                        total_new += 1
                        found += 1
                    if err:
                        total_errs += 1
                if stop:
                    break
                if first_run and page >= FIRST_RUN_MAX_PAGES:
                    break
                page += 1

            ctx.seen.mark_search_initialized(SOURCE, state_key)
            ctx.seen.commit()
            ctx.logger.info(f"FA search '{query}': {found} new")

        return total_new, total_errs

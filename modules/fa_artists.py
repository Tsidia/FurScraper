from .base import Module
from .fa_common import (
    FIRST_RUN_MAX_PAGES,
    SOURCE,
    make_client,
    process_submission,
)

STATE_PREFIX = "artist:"


class FAArtistsModule(Module):
    KEY = "fa_artists"
    LABEL = "FA Artists"

    def run(self, mod_cfg, ctx):
        artists = [a.strip() for a in mod_cfg.get("artists", []) if a.strip()]
        if not artists:
            ctx.logger.info("FA Artists: no artists configured")
            return 0, 0

        client = make_client(ctx)
        total_new = 0
        total_errs = 0

        for artist in artists:
            state_key = STATE_PREFIX + artist
            first_run = not ctx.seen.search_initialized(SOURCE, state_key)
            page = 1
            found = 0
            stop = False
            ctx.logger.info(f"FA artist: {artist}")

            while not stop:
                try:
                    items = client.gallery(artist, page=page)
                except Exception as e:
                    ctx.logger.error(
                        f"FA gallery fetch failed for {artist} page {page}: {e}"
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
                        stop = True  # gallery is newest-first
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
            ctx.logger.info(f"FA artist {artist}: {found} new")

        return total_new, total_errs

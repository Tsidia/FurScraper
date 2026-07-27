from .base import Module
from .fa_common import (
    FIRST_RUN_MAX_PAGES,
    SOURCE,
    make_client,
    process_submission,
)

# Gallery and scraps are two independent newest-first streams, so each needs its
# own "have we walked this before" state. The gallery keeps the original
# "artist:" prefix so existing installs are not treated as first runs.
FOLDER_STATE = {"gallery": "artist:", "scraps": "scraps:"}


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
            for folder, prefix in FOLDER_STATE.items():
                new, errs = self._walk(client, ctx, artist, folder, prefix)
                total_new += new
                total_errs += errs

        return total_new, total_errs

    def _walk(self, client, ctx, artist, folder, prefix):
        """Walk one artist's gallery or scraps, newest first, until a known item."""
        state_key = prefix + artist
        first_run = not ctx.seen.search_initialized(SOURCE, state_key)
        page = 1
        found = 0
        errs = 0
        stop = False
        ctx.logger.info(f"FA {folder}: {artist}")

        while not stop:
            try:
                items = client.submissions(artist, folder=folder, page=page)
            except Exception as e:
                ctx.logger.error(
                    f"FA {folder} fetch failed for {artist} page {page}: {e}"
                )
                errs += 1
                break
            if not items:
                break
            for item in items:
                sid = item.get("id") if isinstance(item, dict) else item
                if not sid:
                    continue
                if ctx.seen.is_seen(SOURCE, sid):
                    stop = True  # listing is newest-first
                    break
                dl, err = process_submission(sid, client, ctx)
                if dl:
                    found += 1
                if err:
                    errs += 1
            if stop:
                break
            if first_run and page >= FIRST_RUN_MAX_PAGES:
                break
            page += 1

        ctx.seen.mark_search_initialized(SOURCE, state_key)
        ctx.seen.commit()
        ctx.logger.info(f"FA {folder} {artist}: {found} new")
        return found, errs

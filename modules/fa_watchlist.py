from .base import Module
from .fa_common import make_client, process_submission


class FAWatchlistModule(Module):
    KEY = "fa_watchlist"
    LABEL = "FA Watchlist"

    def run(self, mod_cfg, ctx):
        auth = ctx.fa_auth or {}
        if not auth.get("cookie_a") or not auth.get("cookie_b"):
            raise RuntimeError(
                "FA Watchlist requires FA cookies — set them on the FA Auth tab"
            )
        client = make_client(ctx)
        try:
            data = client.notifications_submissions()
        except Exception as e:
            ctx.logger.error(f"FA watchlist fetch failed: {e}")
            raise

        # faexport may return the list under different keys depending on version;
        # handle both shapes.
        items = (
            data.get("new_submissions")
            or data.get("submissions")
            or (data if isinstance(data, list) else [])
        )
        ctx.logger.info(f"FA watchlist: {len(items)} in queue")

        total_new = 0
        total_errs = 0
        for item in items:
            sid = item.get("id") if isinstance(item, dict) else item
            if not sid:
                continue
            dl, err = process_submission(sid, client, ctx)
            if dl:
                total_new += 1
            if err:
                total_errs += 1

        ctx.logger.info(f"FA watchlist: {total_new} new")
        return total_new, total_errs

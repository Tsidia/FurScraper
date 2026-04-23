import sys
import logging
import traceback
from pathlib import Path

from common import load_config, SEEN_DB_PATH, LOG_PATH
from modules.base import Context, SeenStore
from modules.e621_mod import E621Module
from modules.fa_artists import FAArtistsModule
from modules.fa_watchlist import FAWatchlistModule
from modules.fa_search import FASearchModule

MODULES = [E621Module, FAArtistsModule, FAWatchlistModule, FASearchModule]


def notify_failure(msg):
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "FurScraper",
            f"Scraper run failed:\n\n{msg}\n\nSee log at:\n{LOG_PATH}",
        )
        root.destroy()
    except Exception:
        pass


def run_all(cfg, logger):
    """Run every enabled module. Returns (new_count, download_errors, module_failures)."""
    out_dir = Path(cfg["output_dir"])
    out_dir.mkdir(parents=True, exist_ok=True)
    seen = SeenStore(SEEN_DB_PATH)
    ctx = Context(
        logger=logger,
        seen=seen,
        out_dir=out_dir,
        blacklist=cfg.get("blacklist", []),
        fa_auth=cfg.get("fa_auth", {}),
    )
    total_new = 0
    total_errs = 0
    mod_failures = []
    try:
        for cls in MODULES:
            mod_cfg = cfg["modules"].get(cls.KEY, {})
            if not mod_cfg.get("enabled", False):
                continue
            logger.info(f"--- Module: {cls.LABEL} ---")
            try:
                new, errs = cls().run(mod_cfg, ctx)
                total_new += new
                total_errs += errs
                logger.info(f"{cls.LABEL} done: {new} new, {errs} errors")
            except Exception as e:
                logger.exception(f"Module {cls.LABEL} failed")
                mod_failures.append(f"{cls.LABEL}: {e}")
    finally:
        seen.close()
    return total_new, total_errs, mod_failures


def main():
    logging.basicConfig(
        filename=LOG_PATH,
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logger = logging.getLogger()
    logger.info("=== Run start ===")
    try:
        cfg = load_config()
        new, errs, mod_failures = run_all(cfg, logger)
        logger.info(
            f"Run done: {new} new, {errs} errors, {len(mod_failures)} module failures"
        )
        if errs or mod_failures:
            parts = [f"Got {new} new file(s)."]
            if errs:
                parts.append(f"{errs} download/API error(s).")
            if mod_failures:
                parts.append("Module failures:\n - " + "\n - ".join(mod_failures))
            notify_failure("\n".join(parts))
            sys.exit(1)
    except Exception as e:
        logger.error(f"Fatal: {e}\n{traceback.format_exc()}")
        notify_failure(str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()

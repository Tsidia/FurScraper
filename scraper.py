import os
import subprocess
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
    import dialogs

    dialogs.error(
        "FurScraper", f"Scraper run failed:\n\n{msg}\n\nSee log at:\n{LOG_PATH}"
    )


def run_all(cfg, logger, progress=None):
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
            if progress:
                progress(f"Running {cls.LABEL}…")
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


def _child_env():
    """The environment for a copy of ourselves, minus PyInstaller's private
    variables.

    A packaged build unpacks itself into a temp folder and tells its own
    subprocesses where that folder is through the environment. Inherited by a
    second launch, those variables make it skip unpacking and run out of the
    *first* launch's folder instead. The first launch then cannot delete that
    folder when it exits (hence Windows' "failed to remove directory _MEIxxxx"
    complaints) and lingers, waiting, for as long as the second is alive.

    That is fatal here, because the interface is meant to outlive the run that
    started it: --run would never exit, and the scheduled task skips a new run
    while an old one is still going, so scraping would stop after the first
    one. Stripping the variables makes the child unpack its own copy and the
    two processes independent.
    """
    env = dict(os.environ)
    for name in list(env):
        if name.startswith("_PYI") or name == "_MEIPASS2":
            del env[name]
    return env


def _ensure_ui_running(cfg, logger):
    """Bring the interface back if it died, so the bookmark keeps working.

    This runs on the scrape schedule, which makes the hourly task double as a
    watchdog without needing any extra trigger.
    """
    if not cfg.get("keep_ui_running", True):
        return
    try:
        import webapp

        port = int(cfg.get("ui_port") or webapp.DEFAULT_PORT)
        if webapp._ours(port):
            return
        cmd = (
            [sys.executable, "--serve"]
            if getattr(sys, "frozen", False)
            else [sys.executable, str(Path(__file__).resolve().parent / "furscraper.py"), "--serve"]
        )
        flags = 0
        for name in ("DETACHED_PROCESS", "CREATE_NEW_PROCESS_GROUP", "CREATE_NO_WINDOW"):
            flags |= getattr(subprocess, name, 0)
        subprocess.Popen(
            cmd, creationflags=flags, close_fds=True, env=_child_env()
        )
        logger.info("interface was not running; started it")
    except Exception as e:
        logger.warning(f"could not restart the interface: {e}")


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
        _ensure_ui_running(cfg, logger)
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

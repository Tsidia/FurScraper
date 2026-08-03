"""Entry point.

    FurScraper.exe            opens the web UI in your browser
    FurScraper.exe --run      performs one scrape and exits
    FurScraper.exe --serve    starts the interface without opening a browser

Two scheduled tasks use the latter two: --run on an interval to scrape, and
--serve at log-in so the bookmarked address is always live. From source the
equivalents are `python furscraper.py`, `--run` and `--serve`.

Startup is wrapped so an unexpected failure produces a dialog rather than
nothing at all: a windowed build has no console, so without this an early crash
would look exactly like the program refusing to open.
"""
import sys

import dialogs


def _fatal(exc):
    import traceback

    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    dialogs.error("FurScraper failed to start", f"{exc}\n\n{detail[-1200:]}")
    sys.exit(1)


def main():
    args = sys.argv[1:]
    try:
        if "--run" in args:
            import scraper

            scraper.main()
        elif "--serve" in args:
            import webapp

            webapp.main(open_browser=False)
        else:
            import webapp

            webapp.main()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 - last resort, must not fail silently
        _fatal(e)


if __name__ == "__main__":
    main()

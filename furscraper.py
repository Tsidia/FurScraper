"""Single entry point for the packaged executable.

    FurScraper.exe          opens the configuration window
    FurScraper.exe --run    performs one scrape and exits

The scheduled task uses `--run`. When running from source the equivalents are
`python config_gui.py` and `python scraper.py`, which still work unchanged.

Startup is wrapped so that an unexpected failure produces a dialog rather than
nothing at all. A windowed build has no console, so without this an early crash
would look exactly like the program refusing to open.
"""
import sys


def _fatal(exc):
    import traceback

    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    try:
        import tkinter as tk
        from tkinter import messagebox

        root = tk.Tk()
        root.withdraw()
        messagebox.showerror(
            "FurScraper failed to start",
            f"{exc}\n\nDetails:\n{detail[-1500:]}",
        )
        root.destroy()
    except Exception:
        # No tkinter to complain with; fall back to stderr for source runs.
        sys.stderr.write(detail)
    sys.exit(1)


def main():
    try:
        if "--run" in sys.argv[1:]:
            import scraper

            scraper.main()
        else:
            import config_gui

            config_gui.main()
    except SystemExit:
        raise
    except BaseException as e:  # noqa: BLE001 - last resort, must not swallow silently
        _fatal(e)


if __name__ == "__main__":
    main()

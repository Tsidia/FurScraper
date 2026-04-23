import os
import sys
import threading
import subprocess
import logging
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

from common import load_config, save_config, APPDATA, LOG_PATH
import scraper

TASK_NAME = "FurScraper"


def pythonw_path():
    p = Path(sys.executable).with_name("pythonw.exe")
    return p if p.exists() else Path(sys.executable)


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("FurScraper — Configuration")
        self.geometry("740x640")
        self.cfg = load_config()
        self._build()

    # ---------- Layout ----------

    def _build(self):
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=8, pady=(8, 0))

        self._build_e621_tab(nb)
        self._build_fa_artists_tab(nb)
        self._build_fa_watchlist_tab(nb)
        self._build_fa_search_tab(nb)
        self._build_fa_auth_tab(nb)
        self._build_blacklist_tab(nb)
        self._build_schedule_tab(nb)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=8, pady=8)
        ttk.Button(btns, text="Save & Schedule", command=self._save).pack(side="left")
        ttk.Button(btns, text="Run Now", command=self._run_now).pack(side="left", padx=6)
        ttk.Button(btns, text="Open Log", command=self._open_log).pack(side="left")
        ttk.Button(btns, text="Open Gallery", command=self._open_gallery).pack(
            side="left", padx=6
        )
        ttk.Button(btns, text="Open Output Folder", command=self._open_output).pack(
            side="left"
        )
        ttk.Button(btns, text="Close", command=self.destroy).pack(side="right")

        self.status = tk.StringVar(value="Ready.")
        ttk.Label(self, textvariable=self.status, foreground="gray", anchor="w").pack(
            side="bottom", fill="x", padx=8, pady=4
        )

    def _build_e621_tab(self, nb):
        m = self.cfg["modules"]["e621"]
        f = ttk.Frame(nb)
        nb.add(f, text="e621")
        self.e621_enabled = tk.BooleanVar(value=m.get("enabled", False))
        ttk.Checkbutton(
            f, text="Enable e621 module", variable=self.e621_enabled
        ).pack(anchor="w", padx=8, pady=6)

        grid = ttk.Frame(f)
        grid.pack(fill="x", padx=8)
        ttk.Label(grid, text="Username:").grid(row=0, column=0, sticky="w", pady=4)
        self.e621_user = tk.StringVar(value=m.get("username", ""))
        ttk.Entry(grid, textvariable=self.e621_user, width=40).grid(
            row=0, column=1, sticky="we", padx=6, pady=4
        )
        ttk.Label(grid, text="API Key:").grid(row=1, column=0, sticky="w", pady=4)
        self.e621_key = tk.StringVar(value=m.get("api_key", ""))
        ttk.Entry(grid, textvariable=self.e621_key, width=40, show="*").grid(
            row=1, column=1, sticky="we", padx=6, pady=4
        )
        grid.columnconfigure(1, weight=1)

        ttk.Label(
            f,
            text="Tag combinations — one search per line (e621 tag syntax; '-' to exclude):",
        ).pack(anchor="w", padx=8, pady=(10, 2))
        self.e621_searches = tk.Text(f, height=12, wrap="word")
        self.e621_searches.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.e621_searches.insert("1.0", "\n".join(m.get("searches", [])))

    def _build_fa_artists_tab(self, nb):
        m = self.cfg["modules"]["fa_artists"]
        f = ttk.Frame(nb)
        nb.add(f, text="FA Artists")
        self.fa_art_enabled = tk.BooleanVar(value=m.get("enabled", False))
        ttk.Checkbutton(
            f, text="Enable FA Artists module", variable=self.fa_art_enabled
        ).pack(anchor="w", padx=8, pady=6)
        ttk.Label(
            f,
            text=(
                "FurAffinity usernames, one per line. Each artist's gallery is scraped "
                "newest-first, stopping when a known submission is hit."
            ),
            justify="left",
        ).pack(anchor="w", padx=8, pady=(4, 2))
        self.fa_artists = tk.Text(f, height=16, wrap="word")
        self.fa_artists.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.fa_artists.insert("1.0", "\n".join(m.get("artists", [])))

    def _build_fa_watchlist_tab(self, nb):
        m = self.cfg["modules"]["fa_watchlist"]
        f = ttk.Frame(nb)
        nb.add(f, text="FA Watchlist")
        self.fa_wl_enabled = tk.BooleanVar(value=m.get("enabled", False))
        ttk.Checkbutton(
            f, text="Enable FA Watchlist module", variable=self.fa_wl_enabled
        ).pack(anchor="w", padx=8, pady=6)
        ttk.Label(
            f,
            text=(
                "Pulls new submissions from every user you follow on FurAffinity.\n\n"
                "This endpoint is always authenticated — fill in FA Auth."
            ),
            justify="left",
        ).pack(anchor="w", padx=8, pady=6)

    def _build_fa_search_tab(self, nb):
        m = self.cfg["modules"]["fa_search"]
        f = ttk.Frame(nb)
        nb.add(f, text="FA Search")
        self.fa_search_enabled = tk.BooleanVar(value=m.get("enabled", False))
        ttk.Checkbutton(
            f, text="Enable FA Search module", variable=self.fa_search_enabled
        ).pack(anchor="w", padx=8, pady=6)
        ttk.Label(
            f,
            text=(
                "FurAffinity keyword searches — one query per line.\n"
                "Results are ordered by date, newest first.\n"
                "Heads up: FA search is noisier than e621 tag search."
            ),
            justify="left",
        ).pack(anchor="w", padx=8, pady=(4, 2))
        self.fa_queries = tk.Text(f, height=16, wrap="word")
        self.fa_queries.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.fa_queries.insert("1.0", "\n".join(m.get("queries", [])))

    def _build_fa_auth_tab(self, nb):
        a = self.cfg.get("fa_auth", {})
        f = ttk.Frame(nb)
        nb.add(f, text="FA Auth")
        ttk.Label(
            f,
            text=(
                "FurAffinity session cookies — required for mature content and for the Watchlist module.\n\n"
                "How to get them:\n"
                "  1. Log into furaffinity.net in your browser.\n"
                "  2. Open DevTools (F12) → Application/Storage → Cookies → www.furaffinity.net\n"
                "  3. Copy the VALUES of the cookies named 'a' and 'b' and paste them below.\n\n"
                "These cookies stay valid as long as your FA session does (typically months).\n"
                "They break if you log out, clear cookies, change password, or FA forces reauth.\n"
                "When that happens, grab fresh values."
            ),
            justify="left",
        ).pack(anchor="w", padx=8, pady=8)

        grid = ttk.Frame(f)
        grid.pack(fill="x", padx=8, pady=6)
        ttk.Label(grid, text="Cookie 'a':").grid(row=0, column=0, sticky="w", pady=4)
        self.fa_cookie_a = tk.StringVar(value=a.get("cookie_a", ""))
        ttk.Entry(grid, textvariable=self.fa_cookie_a, width=60, show="*").grid(
            row=0, column=1, sticky="we", padx=6, pady=4
        )
        ttk.Label(grid, text="Cookie 'b':").grid(row=1, column=0, sticky="w", pady=4)
        self.fa_cookie_b = tk.StringVar(value=a.get("cookie_b", ""))
        ttk.Entry(grid, textvariable=self.fa_cookie_b, width=60, show="*").grid(
            row=1, column=1, sticky="we", padx=6, pady=4
        )
        grid.columnconfigure(1, weight=1)

    def _build_blacklist_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="Blacklist")
        ttk.Label(
            f,
            text=(
                "Blacklisted tags — one per line. Applies to every module.\n"
                "Match is case-insensitive. FA tags are user-supplied and less reliable than e621's."
            ),
            justify="left",
        ).pack(anchor="w", padx=8, pady=6)
        self.bl_text = tk.Text(f, height=16, wrap="word")
        self.bl_text.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.bl_text.insert("1.0", "\n".join(self.cfg.get("blacklist", [])))

    def _build_schedule_tab(self, nb):
        f = ttk.Frame(nb)
        nb.add(f, text="Schedule & Output")
        ttk.Label(f, text="Run interval (minutes):").grid(
            row=0, column=0, sticky="w", padx=8, pady=8
        )
        self.interval_var = tk.IntVar(value=self.cfg.get("interval_minutes", 60))
        ttk.Spinbox(
            f, from_=1, to=10080, textvariable=self.interval_var, width=10
        ).grid(row=0, column=1, sticky="w", padx=8, pady=8)
        ttk.Label(f, text="Output folder:").grid(
            row=1, column=0, sticky="w", padx=8, pady=8
        )
        self.out_var = tk.StringVar(value=self.cfg.get("output_dir", ""))
        of = ttk.Frame(f)
        of.grid(row=1, column=1, sticky="we", padx=8, pady=8)
        ttk.Entry(of, textvariable=self.out_var).pack(side="left", fill="x", expand=True)
        ttk.Button(of, text="Browse…", command=self._browse).pack(side="left", padx=4)
        f.columnconfigure(1, weight=1)
        ttk.Label(
            f,
            text=(
                f"Config and download history are stored at:\n{APPDATA}\n\n"
                "The scheduled task runs only while you are logged in."
            ),
            foreground="gray",
            justify="left",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=8, pady=16)

    # ---------- Actions ----------

    def _browse(self):
        p = filedialog.askdirectory(
            initialdir=self.out_var.get() or str(Path.home())
        )
        if p:
            self.out_var.set(p)

    def _open_log(self):
        if LOG_PATH.exists():
            os.startfile(LOG_PATH)
        else:
            messagebox.showinfo(
                "No log yet", "Log file doesn't exist yet. Run the scraper once first."
            )

    def _open_output(self):
        p = Path(self.out_var.get())
        if p.exists():
            os.startfile(p)
        else:
            messagebox.showinfo("No output folder", f"Folder does not exist yet:\n{p}")

    def _open_gallery(self):
        p = Path(self.out_var.get())
        if not p.exists():
            messagebox.showinfo(
                "No output folder",
                f"Folder does not exist yet:\n{p}\n\nRun the scraper once first.",
            )
            return
        try:
            import gallery
            url = gallery.open_gallery(p)
            self.status.set(f"Gallery opened at {url}")
        except Exception as e:
            messagebox.showerror("Gallery failed to open", str(e))

    def _collect(self):
        def lines(t):
            return [l.strip() for l in t.get("1.0", "end").splitlines() if l.strip()]

        return {
            "output_dir": self.out_var.get().strip(),
            "interval_minutes": int(self.interval_var.get()),
            "blacklist": lines(self.bl_text),
            "fa_auth": {
                "cookie_a": self.fa_cookie_a.get().strip(),
                "cookie_b": self.fa_cookie_b.get().strip(),
            },
            "modules": {
                "e621": {
                    "enabled": bool(self.e621_enabled.get()),
                    "username": self.e621_user.get().strip(),
                    "api_key": self.e621_key.get().strip(),
                    "searches": lines(self.e621_searches),
                },
                "fa_artists": {
                    "enabled": bool(self.fa_art_enabled.get()),
                    "artists": lines(self.fa_artists),
                },
                "fa_watchlist": {
                    "enabled": bool(self.fa_wl_enabled.get()),
                },
                "fa_search": {
                    "enabled": bool(self.fa_search_enabled.get()),
                    "queries": lines(self.fa_queries),
                },
            },
        }

    def _validate(self, cfg):
        if cfg["interval_minutes"] < 1:
            messagebox.showwarning("Bad interval", "Interval must be at least 1 minute.")
            return False
        if not cfg["output_dir"]:
            messagebox.showwarning(
                "Missing output folder", "Choose an output folder on the Schedule & Output tab."
            )
            return False
        mods = cfg["modules"]
        if mods["e621"]["enabled"]:
            if not mods["e621"]["username"] or not mods["e621"]["api_key"]:
                messagebox.showwarning(
                    "e621 credentials missing",
                    "The e621 module is enabled but username or API key is empty.",
                )
                return False
        if mods["fa_watchlist"]["enabled"]:
            if not cfg["fa_auth"]["cookie_a"] or not cfg["fa_auth"]["cookie_b"]:
                messagebox.showwarning(
                    "FA cookies missing",
                    "FA Watchlist is enabled but FA cookies are empty. Fill in the FA Auth tab.",
                )
                return False
        if not any(m.get("enabled") for m in mods.values()):
            if not messagebox.askyesno(
                "No modules enabled",
                "No modules are enabled; scheduled runs will do nothing. Save anyway?",
            ):
                return False
        return True

    def _save(self):
        cfg = self._collect()
        if not self._validate(cfg):
            return
        save_config(cfg)
        try:
            self._register_task(cfg["interval_minutes"])
        except Exception as e:
            messagebox.showerror(
                "Scheduling failed",
                f"Configuration saved, but scheduling the task failed:\n\n{e}",
            )
            return
        self.status.set(
            f"Saved. Scheduled every {cfg['interval_minutes']} minute(s)."
        )
        messagebox.showinfo(
            "Saved",
            f"Configuration saved.\nScheduled task '{TASK_NAME}' runs every "
            f"{cfg['interval_minutes']} minute(s) while you are logged in.",
        )

    def _register_task(self, interval_minutes):
        pw = pythonw_path()
        script = Path(__file__).resolve().parent / "scraper.py"
        # Point the task at pythonw.exe directly — no .bat, no cmd.exe console window.
        tr_value = f'"{pw}" "{script}"'
        cmd = [
            "schtasks", "/Create", "/F",
            "/TN", TASK_NAME,
            "/TR", tr_value,
            "/SC", "MINUTE",
            "/MO", str(interval_minutes),
        ]
        r = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            creationflags=subprocess.CREATE_NO_WINDOW
            if hasattr(subprocess, "CREATE_NO_WINDOW")
            else 0,
        )
        if r.returncode != 0:
            raise RuntimeError(
                (r.stderr or r.stdout or "schtasks failed").strip()
            )
        legacy_bat = APPDATA / "run_scraper.bat"
        if legacy_bat.exists():
            try:
                legacy_bat.unlink()
            except OSError:
                pass

    def _run_now(self):
        cfg = self._collect()
        if not self._validate(cfg):
            return
        save_config(cfg)
        self.status.set("Running… (window stays responsive)")
        self.update_idletasks()

        def worker():
            logging.basicConfig(
                filename=LOG_PATH,
                level=logging.INFO,
                format="%(asctime)s %(levelname)s %(message)s",
            )
            logger = logging.getLogger()
            logger.info("=== Manual run start ===")
            try:
                new, errs, mod_failures = scraper.run_all(cfg, logger)
                logger.info(
                    f"Manual run done: {new} new, {errs} errors, "
                    f"{len(mod_failures)} module failures"
                )
                self.after(0, lambda: self._run_done(new, errs, mod_failures, None))
            except Exception as e:
                logger.exception("Manual run failed")
                self.after(0, lambda: self._run_done(0, 0, [], e))

        threading.Thread(target=worker, daemon=True).start()

    def _run_done(self, new, errs, mod_failures, err):
        if err:
            self.status.set(f"Run failed: {err}")
            messagebox.showerror("Run failed", str(err))
            return
        summary = f"Downloaded {new} new file(s).\n{errs} download/API error(s)."
        if mod_failures:
            summary += "\n\nModule failures:\n - " + "\n - ".join(mod_failures)
        self.status.set(
            f"Run complete — {new} new, {errs} errors, {len(mod_failures)} module failures."
        )
        if mod_failures or errs:
            messagebox.showwarning("Done (with errors)", summary)
        else:
            messagebox.showinfo("Done", summary)


if __name__ == "__main__":
    App().mainloop()

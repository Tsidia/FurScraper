import os
import json
import copy
from pathlib import Path

APP_NAME = "FurScraper"
APPDATA = Path(os.environ.get("APPDATA", Path.home())) / APP_NAME
APPDATA.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = APPDATA / "config.json"
SEEN_DB_PATH = APPDATA / "seen.db"
LOG_PATH = APPDATA / "scraper.log"
# The web UI's access key, kept out of config.json so that exporting or sharing
# a config never leaks it, and so rewriting config cannot lose it.
UI_KEY_PATH = APPDATA / "ui.key"

DEFAULT_CONFIG = {
    "output_dir": str(Path.home() / "Pictures" / "FurScraper"),
    "interval_minutes": 60,
    # Fixed so the UI's address never changes and can be bookmarked. Below
    # 49152, which is where Windows starts handing out ephemeral ports, so it
    # will not be taken at random by something else.
    "ui_port": 47821,
    # Keep the interface reachable at that address from log-in onwards, so the
    # bookmark always works. Off means the UI only exists while you have it
    # open, and closes with the tab.
    "keep_ui_running": True,
    "blacklist": [],
    "fa_auth": {"cookie_a": "", "cookie_b": ""},
    "modules": {
        "e621": {
            "enabled": False,
            "username": "",
            "api_key": "",
            "searches": [],
        },
        "fa_artists": {"enabled": False, "artists": []},
        "fa_watchlist": {"enabled": False},
        "fa_search": {"enabled": False, "queries": []},
    },
}


def _migrate_v1(data):
    """Old flat schema (pre-modules) → nested schema. No-op if already migrated."""
    if "modules" in data:
        return data
    return {
        "output_dir": data.get("output_dir", DEFAULT_CONFIG["output_dir"]),
        "interval_minutes": data.get("interval_minutes", 60),
        "blacklist": data.get("blacklist", []),
        "fa_auth": {"cookie_a": "", "cookie_b": ""},
        "modules": {
            "e621": {
                "enabled": True,  # existing users had e621 configured — keep enabled
                "username": data.get("username", ""),
                "api_key": data.get("api_key", ""),
                "searches": data.get("searches", []),
            },
            "fa_artists": {"enabled": False, "artists": []},
            "fa_watchlist": {"enabled": False},
            "fa_search": {"enabled": False, "queries": []},
        },
    }


def _merge_defaults(cfg):
    merged = copy.deepcopy(DEFAULT_CONFIG)

    def merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = v

    merge(merged, cfg)
    return merged


def load_config():
    if not CONFIG_PATH.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return _merge_defaults(_migrate_v1(data))
    except Exception:
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)

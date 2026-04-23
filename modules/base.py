import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, List


class SeenStore:
    """SQLite-backed dedup store. Keys are (source, post_id)."""

    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self._migrate()

    def _migrate(self):
        c = self.conn
        cur = c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='seen'")
        has_seen = cur.fetchone() is not None
        if has_seen:
            cols = [r[1] for r in c.execute("PRAGMA table_info(seen)").fetchall()]
            if "source" not in cols:
                # v1 → v2: add source column; tag all existing rows as e621.
                has_state = (
                    c.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name='search_state'"
                    ).fetchone()
                    is not None
                )
                c.execute("ALTER TABLE seen RENAME TO seen_v1")
                if has_state:
                    c.execute("ALTER TABLE search_state RENAME TO search_state_v1")
                self._create_tables()
                c.execute(
                    "INSERT OR IGNORE INTO seen(source, post_id, seen_at) "
                    "SELECT 'e621', CAST(post_id AS TEXT), seen_at FROM seen_v1"
                )
                if has_state:
                    c.execute(
                        "INSERT OR IGNORE INTO search_state(source, query, initialized) "
                        "SELECT 'e621', query, initialized FROM search_state_v1"
                    )
                c.execute("DROP TABLE seen_v1")
                if has_state:
                    c.execute("DROP TABLE search_state_v1")
                c.commit()
        else:
            self._create_tables()
            c.commit()

    def _create_tables(self):
        c = self.conn
        c.execute(
            """CREATE TABLE IF NOT EXISTS seen (
                source TEXT NOT NULL,
                post_id TEXT NOT NULL,
                seen_at INTEGER,
                PRIMARY KEY (source, post_id)
            )"""
        )
        c.execute(
            """CREATE TABLE IF NOT EXISTS search_state (
                source TEXT NOT NULL,
                query TEXT NOT NULL,
                initialized INTEGER,
                PRIMARY KEY (source, query)
            )"""
        )

    def is_seen(self, source, post_id):
        cur = self.conn.execute(
            "SELECT 1 FROM seen WHERE source=? AND post_id=?",
            (source, str(post_id)),
        )
        return cur.fetchone() is not None

    def mark_seen(self, source, post_id):
        self.conn.execute(
            "INSERT OR IGNORE INTO seen(source, post_id, seen_at) VALUES (?, ?, ?)",
            (source, str(post_id), int(time.time())),
        )

    def search_initialized(self, source, query):
        cur = self.conn.execute(
            "SELECT initialized FROM search_state WHERE source=? AND query=?",
            (source, query),
        )
        row = cur.fetchone()
        return row is not None and row[0] == 1

    def mark_search_initialized(self, source, query):
        self.conn.execute(
            "INSERT OR REPLACE INTO search_state(source, query, initialized) VALUES (?, ?, 1)",
            (source, query),
        )

    def commit(self):
        self.conn.commit()

    def close(self):
        self.conn.close()


@dataclass
class Context:
    logger: Any
    seen: SeenStore
    out_dir: Any
    blacklist: List[str] = field(default_factory=list)
    fa_auth: dict = field(default_factory=dict)


class Module:
    KEY = ""
    LABEL = ""

    def run(self, mod_cfg, ctx):
        """Return (new_count, error_count). Raise on fatal module-level failure."""
        raise NotImplementedError

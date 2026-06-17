import hashlib
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
        # file_hash is independent of the seen/search_state migration above and
        # may be missing on any older DB; ensure it exists unconditionally.
        self._ensure_hash_table()
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
        self._ensure_hash_table()

    def _ensure_hash_table(self):
        # Content-addressed store of downloaded files. Keyed on the SHA-256 of the
        # raw bytes, so two rows can only collide when the files are byte-for-byte
        # identical; visually-similar alternate versions never share a hash.
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS file_hash (
                sha256 TEXT PRIMARY KEY,
                source TEXT,
                post_id TEXT,
                path TEXT,
                size INTEGER,
                hashed_at INTEGER
            )"""
        )

    def hash_path(self, sha256):
        """Return the stored filename for this content hash, or None if unseen."""
        cur = self.conn.execute(
            "SELECT path FROM file_hash WHERE sha256=?", (sha256,)
        )
        row = cur.fetchone()
        return row[0] if row else None

    def record_hash(self, sha256, source, post_id, path, size):
        self.conn.execute(
            "INSERT OR IGNORE INTO file_hash(sha256, source, post_id, path, size, hashed_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (sha256, source, str(post_id), path, int(size), int(time.time())),
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


def save_download(ctx, source, post_id, data, target):
    """Write `data` to `target`, unless a byte-for-byte identical file already exists.

    Returns True if a new file was written, False if the content was a duplicate of
    something already in the gallery (in which case nothing is written). Matching is
    exact-hash only, so distinct alternate versions are never treated as duplicates.
    """
    sha = hashlib.sha256(data).hexdigest()
    existing = ctx.seen.hash_path(sha)
    if existing is not None:
        ctx.logger.info(
            f"{source} {post_id}: identical content to {existing}, skipped"
        )
        return False
    target.write_bytes(data)
    ctx.seen.record_hash(sha, source, post_id, target.name, len(data))
    return True


class Module:
    KEY = ""
    LABEL = ""

    def run(self, mod_cfg, ctx):
        """Return (new_count, error_count). Raise on fatal module-level failure."""
        raise NotImplementedError

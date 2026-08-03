"""Paired-device sessions and one-time pairing codes.

A new device pairs once, with a short-lived 6-digit code shown on the computer
running FurScraper (scanned as a QR or typed in). Success stores a long-lived
session token server-side; the browser keeps it in a cookie, so the address
stays clean and revoking access is a server-side delete rather than a hunt
through bookmarks.

The master key (ui.key) remains valid alongside these sessions: it is what the
app's own browser launch and old bookmarks carry.
"""
import json
import secrets
import threading
import time

from common import SESSIONS_PATH

COOKIE_NAME = "fs_session"
CODE_TTL = 600        # seconds a pairing code stays valid
MAX_ATTEMPTS = 5      # wrong guesses before the code is void

_lock = threading.Lock()
_sessions = None      # token -> {"created": ts, "device": user-agent}
_code = None          # {"value", "expires", "attempts"}


def _load_locked():
    global _sessions
    if _sessions is not None:
        return
    try:
        rows = json.loads(SESSIONS_PATH.read_text(encoding="utf-8"))
        _sessions = {
            r["token"]: {"created": r.get("created", 0), "device": r.get("device", "")}
            for r in rows
            if isinstance(r, dict) and r.get("token")
        }
    except Exception:
        _sessions = {}


def _save_locked():
    rows = [{"token": t, **meta} for t, meta in _sessions.items()]
    SESSIONS_PATH.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def valid(token):
    if not token:
        return False
    with _lock:
        _load_locked()
        return any(secrets.compare_digest(token, t) for t in _sessions)


def create(device=""):
    with _lock:
        _load_locked()
        token = secrets.token_urlsafe(24)
        _sessions[token] = {"created": time.time(), "device": (device or "")[:120]}
        _save_locked()
        return token


def count():
    with _lock:
        _load_locked()
        return len(_sessions)


def forget_all():
    """Revoke every paired device. The computer's own browser re-pairs
    silently through the master-key launch URL."""
    global _sessions
    with _lock:
        _load_locked()
        n = len(_sessions)
        _sessions = {}
        _save_locked()
        return n


def new_code():
    """Replaces any previous code; only one pairing can be open at a time."""
    global _code
    with _lock:
        _code = {
            "value": f"{secrets.randbelow(1_000_000):06d}",
            "expires": time.time() + CODE_TTL,
            "attempts": 0,
        }
        return _code["value"]


def current_code():
    with _lock:
        if _code and time.time() <= _code["expires"]:
            return _code["value"]
        return None


def redeem(guess, device=""):
    """Returns (token, error). A six-digit code with five attempts and a ten
    minute life is deliberately not brute-forceable; the sleep below just makes
    the arithmetic even less interesting."""
    global _code
    guess = (guess or "").strip()
    delay = False
    with _lock:
        if not _code or time.time() > _code["expires"]:
            _code = None
            token, error = None, (
                "No pairing code is active. On the computer running FurScraper, "
                "open Settings and click 'Pair a device'."
            )
        elif _code["attempts"] >= MAX_ATTEMPTS:
            _code = None
            token, error = None, "Too many wrong attempts. Generate a new code."
        elif not secrets.compare_digest(guess, _code["value"]):
            _code["attempts"] += 1
            delay = True
            token, error = None, "Wrong code."
        else:
            _code = None
            token, error = "issue", None
    if delay:
        time.sleep(0.4)
    if token:
        token = create(device)
    return token, error


def cookie_header(token):
    # SameSite=Lax keeps the cookie out of cross-site POSTs, which together
    # with the server's Origin check is the CSRF story. No Secure flag: this
    # is plain HTTP on a trusted network by design.
    return f"{COOKIE_NAME}={token}; Path=/; Max-Age=31536000; HttpOnly; SameSite=Lax"

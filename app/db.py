"""SQLite plumbing.

Note what is NOT here: there is no `status` column anywhere. The status of a
document is never stored, it is always *derived* by replaying the ledger.
"""
import os
import sqlite3
import threading

_BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.environ.get("SEALTRAIL_DB", os.path.join(_BASE, "data", "sealtrail.db"))
ANCHOR_PATH = os.environ.get("SEALTRAIL_ANCHORS", os.path.join(_BASE, "data", "anchors.log"))

# One writer at a time: makes "two approvers click at the same instant" safe.
WRITE_LOCK = threading.RLock()

SCHEMA = """
CREATE TABLE IF NOT EXISTS users(
    id    TEXT PRIMARY KEY,
    name  TEXT NOT NULL,
    role  TEXT NOT NULL,
    title TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents(
    id         TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS versions(
    doc_id       TEXT NOT NULL,
    version      INTEGER NOT NULL,
    content_json TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    PRIMARY KEY(doc_id, version)
);
CREATE TABLE IF NOT EXISTS ledger(
    doc_id       TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    ts           TEXT NOT NULL,
    version      INTEGER NOT NULL,
    actor        TEXT NOT NULL,
    action       TEXT NOT NULL,
    stage_idx    INTEGER,
    comment      TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL,
    prev_hash    TEXT NOT NULL,
    hash         TEXT NOT NULL,
    PRIMARY KEY(doc_id, seq)
);
"""


def configure(db_path=None, anchor_path=None):
    """Point the app at different files (used by tests)."""
    global DB_PATH, ANCHOR_PATH
    if db_path:
        DB_PATH = db_path
    if anchor_path:
        ANCHOR_PATH = anchor_path


def connect():
    os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
    conn = sqlite3.connect(DB_PATH, timeout=15, isolation_level=None, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = connect()
    try:
        conn.executescript(SCHEMA)
    finally:
        conn.close()


def reset_all():
    """Delete the database AND the external anchor log, then recreate tables."""
    for p in (DB_PATH, ANCHOR_PATH):
        if os.path.exists(p):
            os.remove(p)
    from . import files  # local import: files imports this module
    files.clear()
    init_db()

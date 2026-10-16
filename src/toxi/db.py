"""SQLite storage (see PRD §4.1.2, §4.2.1, §4.5.2).

The daemon is the only writer; CLI clients reach the data through IPC, never by
opening this database directly.
"""
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS contacts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tox_id TEXT UNIQUE,                     -- 76-char full Tox ID; NULL when added via an
                                            -- accepted request (we only learn their public key)
    public_key TEXT NOT NULL UNIQUE,
    alias TEXT NOT NULL UNIQUE,
    display_name TEXT,
    status_message TEXT,
    added_at INTEGER NOT NULL,
    added_by TEXT,
    last_seen INTEGER,
    is_online BOOLEAN DEFAULT 0,
    friend_number INTEGER,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    msg_uuid TEXT NOT NULL UNIQUE,
    contact_id INTEGER NOT NULL,
    direction TEXT NOT NULL,
    msg_type TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    received_at INTEGER,
    status TEXT NOT NULL,
    delivered_at INTEGER,
    read_at INTEGER,
    last_attempt_at INTEGER,                -- when we last (re)sent; drives ACK-timeout retry
    FOREIGN KEY (contact_id) REFERENCES contacts(id)
);

CREATE INDEX IF NOT EXISTS idx_messages_contact ON messages(contact_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);
CREATE INDEX IF NOT EXISTS idx_queue ON messages(contact_id, status, created_at)
    WHERE status = 'queued';

CREATE TABLE IF NOT EXISTS friend_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    public_key TEXT NOT NULL UNIQUE,        -- 64-char public key of the requester
    message TEXT,
    received_at INTEGER NOT NULL,
    status TEXT NOT NULL                     -- 'pending' / 'accepted' / 'rejected'
);

CREATE TABLE IF NOT EXISTS invites (
    nonce TEXT PRIMARY KEY,                 -- one-time `toxi pair` invite (design §4.2)
    created_at INTEGER NOT NULL,
    expires_at INTEGER NOT NULL,
    used_at INTEGER
);

CREATE TABLE IF NOT EXISTS pending_introductions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    from_contact_id INTEGER NOT NULL,
    introduced_tox_id TEXT NOT NULL,
    suggested_alias TEXT,
    note TEXT,
    received_at INTEGER NOT NULL,
    status TEXT NOT NULL,
    FOREIGN KEY (from_contact_id) REFERENCES contacts(id)
);
"""


# Columns added after v0.3.0; applied idempotently to databases created earlier.
_ADDED_COLUMNS = {
    "contacts": [("profile_json", "TEXT"),          # peer's self-described card (design §4.1)
                 ("profile_at", "INTEGER"),
                 ("proto", "INTEGER DEFAULT 1")],   # envelope version the peer speaks
    "messages": [("from_session", "TEXT"),           # sender's session/project label
                 ("part_id", "TEXT"),                 # multi-part text: shared id (design §4.3)
                 ("part_i", "INTEGER"),
                 ("part_n", "INTEGER"),
                 ("read_receipt_at", "INTEGER")],     # outgoing: when the peer reported it read
}


def _migrate(conn: sqlite3.Connection) -> None:
    for table, cols in _ADDED_COLUMNS.items():
        have = {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, decl in cols:
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


def connect(path: Path | str) -> sqlite3.Connection:
    """Open the database, applying schema and pragmas. Idempotent."""
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.executescript(SCHEMA)
    _migrate(conn)
    conn.commit()
    return conn

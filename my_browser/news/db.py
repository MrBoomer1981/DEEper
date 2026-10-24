"""The news part's state: one SQLite file next to the reports (news.sqlite, on the Railway volume).

- sources: what to read. Each has an owner, the organization behind it: four Anthropic feeds or two Habr hubs
  are one owner, so a story counts independent sources, not feeds.
- items: the posts read from them, one row per post and source.
- stories: posts about the same event, merged (stage 4) and scored (stage 5).
- feedback: 👍/👎 on stories (stage 8).
- settings: the user's settings (config.py); state: what the loop remembers; spend: LLM cost per day (stage 5).
"""

import json
import sqlite3
import threading
from contextlib import contextmanager

from my_browser.common.settings import REPORTS_DIR

DB_PATH = REPORTS_DIR / "news.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,              -- rss | hf_papers | telegram
    url TEXT NOT NULL,               -- feed or API address, or the channel's name
    name TEXT NOT NULL,
    category TEXT,                   -- lab | papers | media | analyst | community
    lang TEXT,                       -- en | ru
    owner TEXT NOT NULL,             -- who stands behind it; feeds of one owner count as one source
    is_primary INTEGER DEFAULT 0,    -- first-hand: the lab's own blog, the paper itself
    ai_filter INTEGER DEFAULT 0,     -- a general feed: only its posts about AI are kept
    interval INTEGER DEFAULT 30,     -- minutes between polls
    weight REAL DEFAULT 1.0,
    enabled INTEGER DEFAULT 1,
    added REAL,
    polled REAL, last_ok REAL, last_error TEXT, fails INTEGER DEFAULT 0,
    etag TEXT, modified TEXT,        -- conditional GET: an unchanged feed answers 304
    UNIQUE (kind, url)
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY,
    source_id INTEGER NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT, title TEXT, text TEXT,
    links TEXT,                      -- JSON list of the addresses the post links to: a shared link means the same story
    published REAL,                  -- the source's date, or when the post was first seen
    seen REAL NOT NULL,
    lang TEXT,
    backfill INTEGER DEFAULT 0,      -- an old post found on a source's first poll: known, never news
    embedding BLOB,
    story_id INTEGER,
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS items_published ON items (published);
CREATE INDEX IF NOT EXISTS items_story ON items (story_id);
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY,
    first_seen REAL, last_updated REAL,
    title TEXT, topic TEXT, importance INTEGER, is_ai INTEGER, summary_ru TEXT, reason TEXT,
    has_primary INTEGER DEFAULT 0,
    source_count INTEGER DEFAULT 0,  -- independent sources: distinct owners
    status TEXT DEFAULT 'new',       -- new | queued | sent | dropped
    drop_reason TEXT, sent_at REAL
);
CREATE INDEX IF NOT EXISTS stories_first_seen ON stories (first_seen);
CREATE TABLE IF NOT EXISTS feedback (story_id INTEGER NOT NULL, value INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, rub REAL DEFAULT 0, calls INTEGER DEFAULT 0);
"""
# Columns newer than a table in an older news.sqlite: {table: {column: type}}, added on first use.
ADDED_COLUMNS: dict[str, dict[str, str]] = {}

_write = threading.Lock()  # the loop's thread, the API and the pages write from different threads
_ready = False


def _prepare(db: sqlite3.Connection) -> None:
    global _ready
    if _ready:
        return
    db.execute("PRAGMA journal_mode=WAL")  # the pages read while the loop writes
    db.executescript(SCHEMA)
    for table, columns in ADDED_COLUMNS.items():
        present = {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in present:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
    _ready = True


@contextmanager
def connect():
    """A connection for reading; the schema is created on first use."""
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        _prepare(db)
        yield db
    finally:
        db.close()


@contextmanager
def transaction():
    """A connection for writing, committed at the end; one writer at a time in this process."""
    with _write, connect() as db:
        yield db
        db.commit()


def get_state(key: str, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_state(key: str, value) -> None:
    with transaction() as db:
        db.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                   (key, json.dumps(value, ensure_ascii=False)))

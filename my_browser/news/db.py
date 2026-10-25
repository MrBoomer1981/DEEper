"""The news part's state: one SQLite file next to the reports (news.sqlite, on the Railway volume).

- sources: what to read. Each has an owner, the organization behind it: four Anthropic feeds or two Habr hubs
  are one owner, so a story counts independent sources, not feeds.
- items: the posts read from them, one row per post and source.
- stories: posts about the same event, merged (stage 4) and scored (stage 5).
- feedback: 👍/👎 on stories (stage 8).
- settings: the user's settings (config.py); state: what the loop remembers; spend: LLM cost per day (stage 5).

Two lanes share the tables: `news` and `opps`, the opportunities (hackathons, schools,
internships, grants) with their deadlines. A source, its posts and the stories made of them carry the lane; posts
merge only within their lane. An opportunity's details are in `opportunities` (one row per story of the opps lane),
the reminders already sent in `opp_reminders`, the lane's LLM cost in `opps_spend`, apart from the news budget.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from my_browser.common.settings import REPORTS_DIR

DB_PATH = REPORTS_DIR / "news.sqlite"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sources (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,              -- rss | hf_papers | telegram | page
    url TEXT NOT NULL,               -- feed or API address, the channel's name, or a page to watch
    name TEXT NOT NULL,
    lane TEXT DEFAULT 'news',        -- news | opps
    category TEXT,                   -- news: lab | papers | media | analyst | community;
                                     -- opps: aggregator | company | university | program | conference | community
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
    page_hash TEXT,                  -- a watched page: the fingerprint of its main text at the last poll,
    page_text TEXT,                  --   and the text, to tell what changed at the next one
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
    lane TEXT DEFAULT 'news',        -- its source's lane
    backfill INTEGER DEFAULT 0,      -- an old post found on a source's first poll: known, never news
    digest INTEGER DEFAULT 0,        -- several stories in one post (a channel's roundup): out of merging and scoring
    split_at REAL,                   -- opps: when a roundup was cut into one post per opportunity (opps.split)
    copied_from INTEGER,             -- opps: the post this one repeats (a roundup's part, a news story's announcement)
    embedding BLOB,
    story_id INTEGER,
    UNIQUE (source_id, external_id)
);
CREATE INDEX IF NOT EXISTS items_published ON items (published);
CREATE INDEX IF NOT EXISTS items_story ON items (story_id);
CREATE TABLE IF NOT EXISTS stories (
    id INTEGER PRIMARY KEY,
    lane TEXT DEFAULT 'news',        -- its posts' lane
    first_seen REAL, last_updated REAL,
    title TEXT,                      -- the best post's title (dedup.py); title_ru is the model's
    topic TEXT, importance INTEGER, is_ai INTEGER, summary_ru TEXT, reason TEXT,
    has_primary INTEGER DEFAULT 0,   -- a first-hand source among the posts, or primary_url found by the model
    source_count INTEGER DEFAULT 0,  -- independent sources: distinct owners
    status TEXT DEFAULT 'new',       -- new | queued | sent | dropped
    drop_reason TEXT, sent_at REAL,
    title_ru TEXT, primary_url TEXT,
    scored_count INTEGER,            -- source_count when last scored: it is scored again only when it grew enough
    scored_at REAL,
    update_sent INTEGER DEFAULT 0,   -- a sent story that grew gets one update, never more
    sent_count INTEGER               -- source_count when it was sent
);
CREATE INDEX IF NOT EXISTS stories_first_seen ON stories (first_seen);
CREATE TABLE IF NOT EXISTS feedback (story_id INTEGER NOT NULL, value INTEGER NOT NULL, created REAL NOT NULL);
CREATE TABLE IF NOT EXISTS deliveries (
    id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,              -- urgent | digest
    created REAL NOT NULL,
    text TEXT NOT NULL,              -- Telegram HTML
    buttons TEXT,                    -- JSON rows of [label, callback data]
    story_ids TEXT,                  -- JSON list
    sent_at REAL                     -- when the bot sent it; NULL while it waits
);
CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS state (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS spend (day TEXT PRIMARY KEY, rub REAL DEFAULT 0, calls INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS opportunities (
    story_id INTEGER PRIMARY KEY,    -- a story of the opps lane
    opp_type TEXT,                   -- config.OPP_TYPES
    title_ru TEXT, organizer TEXT,
    summary_ru TEXT,                 -- what it is and what it gives, one or two sentences
    deadline TEXT,                   -- ISO date and time with the time zone, or NULL
    deadline_at REAL,                -- the same in seconds since the epoch, for ordering and reminders
    deadline_kind TEXT,              -- exact | approximate | rolling | unknown
    deadline_text TEXT,              -- as the source writes it
    tz_assumed INTEGER DEFAULT 0,    -- the text named no time zone: Moscow's for a Russian source, else UTC
    starts TEXT, ends TEXT,          -- the event's dates, if any
    format TEXT,                     -- online | offline | hybrid
    location TEXT,
    eligibility TEXT,                -- who may apply, briefly
    ru_access TEXT,                  -- yes | no | unclear: may a Russian citizen living in Russia take part
    cost TEXT, reward TEXT,
    apply_url TEXT,                  -- where to apply, or the official page
    fit INTEGER, fit_reason TEXT,    -- 0-10 against the profile, and why in one line
    status TEXT DEFAULT 'open',      -- open | tracked | hidden | applied | expired | dropped
    drop_reason TEXT,
    checked_at REAL,                 -- when the fields were last extracted
    sent_at REAL                     -- when it went out, in a weekly digest or at once
);
CREATE TABLE IF NOT EXISTS opp_reminders (
    story_id INTEGER NOT NULL, days_before INTEGER NOT NULL, sent_at REAL NOT NULL,
    UNIQUE (story_id, days_before)   -- a reminder never goes out twice
);
CREATE TABLE IF NOT EXISTS opps_spend (day TEXT PRIMARY KEY, rub REAL DEFAULT 0, calls INTEGER DEFAULT 0);
"""
# Columns newer than a table in an older news.sqlite: {table: {column: type}}, added on first use.
ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "sources": {"lane": "TEXT DEFAULT 'news'", "page_hash": "TEXT", "page_text": "TEXT"},
    "items": {"digest": "INTEGER DEFAULT 0", "lane": "TEXT DEFAULT 'news'", "split_at": "REAL", "copied_from": "INTEGER"},
    "stories": {"title_ru": "TEXT", "primary_url": "TEXT", "scored_count": "INTEGER", "scored_at": "REAL",
                "update_sent": "INTEGER DEFAULT 0", "sent_count": "INTEGER", "lane": "TEXT DEFAULT 'news'"},
    "opportunities": {"tz_assumed": "INTEGER DEFAULT 0"},
}

# Run once when a column is added to an older file: posts stored before digests were recognized get the flag.
ON_ADD = {("items", "digest"): "UPDATE items SET digest = 1 WHERE json_array_length(COALESCE(links, '[]')) >= 4 "
                               "AND source_id IN (SELECT id FROM sources WHERE kind = 'telegram')"}

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
                if (table, column) in ON_ADD:
                    db.execute(ON_ADD[(table, column)])
    db.commit()
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


BACKUPS = REPORTS_DIR / "backups"
KEEP_BACKUPS = 7


def backup() -> Path:
    """Today's copy of news.sqlite in REPORTS_DIR/backups, made by SQLite while the loop may write; the last
    KEEP_BACKUPS are kept. They are on the same volume: a copy against a broken file or a bad change, not against
    losing the volume."""
    BACKUPS.mkdir(parents=True, exist_ok=True)
    target = BACKUPS / f"news-{time.strftime('%Y-%m-%d')}.sqlite"
    with connect() as source:
        copy = sqlite3.connect(target)
        try:
            source.backup(copy)
        finally:
            copy.close()
    for old in sorted(BACKUPS.glob("news-*.sqlite"))[:-KEEP_BACKUPS]:
        old.unlink()
    return target


def get_state(key: str, default=None):
    with connect() as db:
        row = db.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
    return json.loads(row["value"]) if row else default


def set_state(key: str, value) -> None:
    with transaction() as db:
        db.execute("INSERT INTO state (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                   (key, json.dumps(value, ensure_ascii=False)))

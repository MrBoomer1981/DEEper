"""Report library: numbered history with full-text search (SQLite FTS5).

Files in reports/ stay the source of truth; the database only indexes them
and is rebuilt from the .json/.md pairs by sync().
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from my_browser.common.report import reading_words
from my_browser.common.settings import REPORTS_DIR
from my_browser.common.text import reading_minutes, slugify

DB_PATH = REPORTS_DIR / "library.sqlite"
REPORT_NAME = re.compile(r"\d{4}-\d{2}-\d{2}_")  # reports are <date>_<time>_<slug>.*


@dataclass
class Entry:
    id: int
    query: str
    mode: str
    created: str
    words: int
    duration_sec: int
    sources_count: int
    base: Path
    reading_words: int = 0  # the words a reader reads: no appended sections, sources or citations

    @property
    def md_path(self) -> Path:
        return self.base.with_suffix(".md")

    @property
    def reading_min(self) -> int:
        return reading_minutes(self.reading_words or self.words)

    def files(self) -> list[Path]:
        return [p for p in (self.base.with_suffix(s) for s in (".pdf", ".docx", ".md")) if p.exists()]


class Library:
    def __init__(self, db_path: Path = DB_PATH):
        db_path.parent.mkdir(exist_ok=True)
        self.db = sqlite3.connect(db_path)
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY,
                base TEXT UNIQUE NOT NULL,
                query TEXT, mode TEXT, created TEXT,
                words INTEGER, duration_sec INTEGER, sources_count INTEGER,
                parent TEXT,  -- file name of the report this one continues
                reading_words INTEGER  -- common.report.reading_words() of its text
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(query, body, tokenize = 'unicode61');
        """)
        present = {row[1] for row in self.db.execute("PRAGMA table_info(reports)")}
        if "parent" not in present:
            self.db.execute("ALTER TABLE reports ADD COLUMN parent TEXT")  # databases from before follow-ups
        if "reading_words" not in present:
            self.db.execute("ALTER TABLE reports ADD COLUMN reading_words INTEGER")  # from before reading times (sync() fills it)

    def add(self, json_path: Path) -> int:
        base = json_path.with_suffix("")
        meta = json.loads(json_path.read_text("utf-8"))
        md = base.with_suffix(".md")
        body = md.read_text("utf-8") if md.exists() else ""
        with self.db:
            row = self.db.execute("SELECT id FROM reports WHERE base = ?", (str(base),)).fetchone()
            if row:
                return row[0]
            cur = self.db.execute(
                "INSERT INTO reports (base, query, mode, created, words, duration_sec, sources_count, parent, reading_words) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (str(base), meta["query"], meta["mode"], meta["created"], meta["words"], meta["duration_sec"],
                 len(meta["sources"]), meta.get("parent"), reading_words(body)),
            )
            self.db.execute("INSERT INTO reports_fts (rowid, query, body) VALUES (?, ?, ?)", (cur.lastrowid, meta["query"], body))
            return cur.lastrowid

    def store(self, files: dict[str, bytes]) -> int:
        """Write a report's files (.md, .json, .docx, .pdf from the worker) and index it; returns its number."""
        meta = json.loads(files[".json"])
        base = REPORTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_{slugify(meta['query'])}"
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        for suffix, data in files.items():
            base.with_suffix(suffix).write_bytes(data)
        return self.add(base.with_suffix(".json"))

    def sync(self) -> None:
        """Index reports made before the library existed (or by an older version), and count the reading words
        of those indexed before reading times."""
        for json_path in sorted(REPORTS_DIR.glob("*.json")):
            if REPORT_NAME.match(json_path.name) and not json_path.name.endswith(".qa.json"):  # not read_stats.json etc.
                self.add(json_path)
        for report_id, base in self.db.execute("SELECT id, base FROM reports WHERE reading_words IS NULL").fetchall():
            md = Path(base).with_suffix(".md")
            with self.db:
                self.db.execute("UPDATE reports SET reading_words = ? WHERE id = ?",
                                (reading_words(md.read_text("utf-8")) if md.exists() else 0, report_id))

    def get(self, report_id: int) -> Entry | None:
        row = self.db.execute(f"SELECT {_COLUMNS} FROM reports WHERE id = ?", (report_id,)).fetchone()
        return _entry(row) if row else None

    def recent(self, limit: int = 20) -> list[Entry]:
        rows = self.db.execute(f"SELECT {_COLUMNS} FROM reports ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [_entry(r) for r in rows]

    def search(self, text: str, limit: int = 5) -> list[tuple[Entry, str]]:
        """Prefix match on every word, so «аккумулятор» also finds «аккумуляторов»."""
        words = re.findall(r"\w+", text.lower())
        if not words:
            return []
        match = " ".join(f'"{w}"*' for w in words)
        columns = ", ".join(f"reports.{c.strip()}" for c in _COLUMNS.split(","))
        rows = self.db.execute(
            f"""SELECT {columns}, snippet(reports_fts, 1, '«', '»', '…', 24)
                FROM reports_fts JOIN reports ON reports.id = reports_fts.rowid
                WHERE reports_fts MATCH ? ORDER BY bm25(reports_fts) LIMIT ?""",
            (match, limit),
        ).fetchall()
        return [(_entry(r[:-1]), r[-1]) for r in rows]

    def by_name(self, name: str) -> Entry | None:
        """The report whose files are reports/<name>.*; parents are stored by name, which survives a rebuilt DB."""
        row = self.db.execute(f"SELECT {_COLUMNS} FROM reports WHERE base = ?", (str(REPORTS_DIR / name),)).fetchone()
        return _entry(row) if row else None

    def children(self, entry: Entry) -> list[Entry]:
        rows = self.db.execute(f"SELECT {_COLUMNS} FROM reports WHERE parent = ? ORDER BY id", (entry.base.name,)).fetchall()
        return [_entry(r) for r in rows]

    def related(self, entry: Entry, exclude: set[int], limit: int = 3) -> list[Entry]:
        """Other reports whose topic shares words with this one (any word, best match first)."""
        words = [w for w in re.findall(r"\w+", entry.query.lower()) if len(w) >= 4 and not w.isdigit()]
        if not words:
            return []
        match = "query : (" + " OR ".join(f'"{w}"*' for w in words) + ")"
        columns = ", ".join(f"reports.{c.strip()}" for c in _COLUMNS.split(","))
        rows = self.db.execute(
            f"""SELECT {columns} FROM reports_fts JOIN reports ON reports.id = reports_fts.rowid
                WHERE reports_fts MATCH ? ORDER BY bm25(reports_fts) LIMIT ?""",
            (match, limit + len(exclude) + 1),
        ).fetchall()
        return [e for e in map(_entry, rows) if e.id != entry.id and e.id not in exclude][:limit]

    def delete(self, report_id: int) -> Entry | None:
        """Remove the entry and its files."""
        entry = self.get(report_id)
        if not entry:
            return None
        with self.db:
            self.db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            self.db.execute("DELETE FROM reports_fts WHERE rowid = ?", (report_id,))
        for suffix in (".md", ".json", ".docx", ".pdf", ".qa.json", ".short.md"):
            entry.base.with_suffix(suffix).unlink(missing_ok=True)
        return entry


_COLUMNS = "id, query, mode, created, words, duration_sec, sources_count, base, reading_words"


def _entry(row) -> Entry:
    return Entry(*row[:7], base=Path(row[7]), reading_words=row[8] or 0)

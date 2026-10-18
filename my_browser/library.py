"""Report library: numbered history with full-text search (SQLite FTS5).

Files in reports/ stay the source of truth; the database only indexes them
and is rebuilt from the .json/.md pairs by sync().
"""

import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from my_browser.pipeline import REPORTS_DIR

DB_PATH = REPORTS_DIR / "library.sqlite"


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

    @property
    def md_path(self) -> Path:
        return self.base.with_suffix(".md")

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
                words INTEGER, duration_sec INTEGER, sources_count INTEGER
            );
            CREATE VIRTUAL TABLE IF NOT EXISTS reports_fts USING fts5(query, body, tokenize = 'unicode61');
        """)

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
                "INSERT INTO reports (base, query, mode, created, words, duration_sec, sources_count) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (str(base), meta["query"], meta["mode"], meta["created"], meta["words"], meta["duration_sec"], len(meta["sources"])),
            )
            self.db.execute("INSERT INTO reports_fts (rowid, query, body) VALUES (?, ?, ?)", (cur.lastrowid, meta["query"], body))
            return cur.lastrowid

    def sync(self) -> None:
        """Index reports made before the library existed (or by an older version)."""
        for json_path in sorted(REPORTS_DIR.glob("*.json")):
            if not json_path.name.endswith(".qa.json"):
                self.add(json_path)

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

    def delete(self, report_id: int) -> Entry | None:
        """Remove the entry and its files."""
        entry = self.get(report_id)
        if not entry:
            return None
        with self.db:
            self.db.execute("DELETE FROM reports WHERE id = ?", (report_id,))
            self.db.execute("DELETE FROM reports_fts WHERE rowid = ?", (report_id,))
        for suffix in (".md", ".json", ".docx", ".pdf", ".qa.json"):
            entry.base.with_suffix(suffix).unlink(missing_ok=True)
        return entry


_COLUMNS = "id, query, mode, created, words, duration_sec, sources_count, base"


def _entry(row) -> Entry:
    return Entry(*row[:7], base=Path(row[7]))

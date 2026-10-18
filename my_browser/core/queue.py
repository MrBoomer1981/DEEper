"""Research jobs, kept in SQLite next to the library so they survive restarts.

Statuses: queued → running → done | error | interrupted. The worker claims the oldest queued job,
reports progress (its heartbeat) and hands in the files or an error. A worker that (re)starts says
hello, and jobs still running under another worker id become interrupted: the process that ran them
is gone. A running job silent for HEARTBEAT_TIMEOUT is interrupted too. Interrupted and failed jobs
can be put back in the queue as they were.
"""

import json
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager

from my_browser.common.settings import REPORTS_DIR

DB_PATH = REPORTS_DIR / "library.sqlite"
HEARTBEAT_TIMEOUT = 300
KEEP_FINISHED = 50
FIRST_PHASE = "Проверяю DeepSeek и поиск..."
JSON_FIELDS = {"queries": [], "urls": [], "log": [], "origin_ref": None}

SCHEMA = """CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY, query TEXT NOT NULL, mode TEXT, sources TEXT, writer TEXT,
    parent TEXT,      -- file name of the report this research continues
    origin TEXT,      -- web | telegram
    origin_ref TEXT,  -- for telegram: {"chat": ..., "message": ...} of the status message
    status TEXT, phase TEXT, created REAL, started REAL, finished REAL, heartbeat REAL, worker TEXT,
    queries TEXT, urls TEXT, log TEXT, cost_rub REAL, report_id INTEGER, error TEXT,
    notified INTEGER DEFAULT 0  -- the bot has told about the interruption
)"""

_write = threading.Lock()  # the web app, the API and the bot write from different threads


@contextmanager
def _db():
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    try:
        db.execute(SCHEMA)
        yield db
        db.commit()
    finally:
        db.close()


def _job(row: sqlite3.Row | None) -> dict | None:
    if row is None:
        return None
    job = dict(row)
    for key, empty in JSON_FIELDS.items():
        job[key] = json.loads(job[key]) if job[key] else empty
    job["elapsed"] = ((job["finished"] or time.time()) - job["started"]) if job["started"] else 0.0
    job["active"] = job["status"] in ("queued", "running")
    return job


def _expire(db: sqlite3.Connection) -> None:
    now = time.time()
    db.execute("UPDATE jobs SET status='interrupted', phase='Прервано', finished=? WHERE status='running' AND heartbeat < ?",
               (now, now - HEARTBEAT_TIMEOUT))


def submit(query: str, mode: str, sources: str, writer: str, parent: str | None = None,
           origin: str = "web", origin_ref: dict | None = None) -> dict:
    job_id = uuid.uuid4().hex[:10]
    with _write, _db() as db:
        db.execute("INSERT INTO jobs (id, query, mode, sources, writer, parent, origin, origin_ref, status, phase, created) "
                   "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'queued', 'В очереди', ?)",
                   (job_id, query, mode, sources, writer, parent, origin, json.dumps(origin_ref) if origin_ref else None, time.time()))
        stale = db.execute("SELECT id FROM jobs WHERE status IN ('done', 'error', 'interrupted') "
                           "ORDER BY created DESC LIMIT -1 OFFSET ?", (KEEP_FINISHED,)).fetchall()
        db.executemany("DELETE FROM jobs WHERE id = ?", [(r["id"],) for r in stale])
        return _job(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def get(job_id: str) -> dict | None:
    with _write, _db() as db:
        _expire(db)
        return _job(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def active() -> list[dict]:
    with _write, _db() as db:
        _expire(db)
        rows = db.execute("SELECT * FROM jobs WHERE status IN ('queued', 'running') ORDER BY created").fetchall()
        return [_job(r) for r in rows]


def interrupted(origin: str, unnotified: bool = True) -> list[dict]:
    with _write, _db() as db:
        _expire(db)
        rows = db.execute("SELECT * FROM jobs WHERE status = 'interrupted' AND origin = ?" + (" AND notified = 0" if unnotified else ""),
                          (origin,)).fetchall()
        return [_job(r) for r in rows]


def mark_notified(job_id: str) -> None:
    with _write, _db() as db:
        db.execute("UPDATE jobs SET notified = 1 WHERE id = ?", (job_id,))


def restart(job_id: str) -> dict | None:
    """Put an interrupted or failed job back in the queue, as it was submitted."""
    with _write, _db() as db:
        db.execute("UPDATE jobs SET status='queued', phase='В очереди', started=NULL, finished=NULL, heartbeat=NULL, "
                   "worker=NULL, queries=NULL, urls=NULL, log=NULL, cost_rub=NULL, error=NULL, notified=0, created=? "
                   "WHERE id = ? AND status IN ('interrupted', 'error')", (time.time(), job_id))
        return _job(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def hello(worker: str) -> None:
    """A worker (re)started: whatever another worker was running will not finish."""
    with _write, _db() as db:
        db.execute("UPDATE jobs SET status='interrupted', phase='Прервано', finished=? WHERE status='running' AND worker != ?",
                   (time.time(), worker))


def claim(worker: str) -> dict | None:
    with _write, _db() as db:
        _expire(db)
        row = db.execute("SELECT id FROM jobs WHERE status = 'queued' ORDER BY created LIMIT 1").fetchone()
        if row is None:
            return None
        now = time.time()
        db.execute("UPDATE jobs SET status='running', worker=?, started=?, heartbeat=?, phase=? WHERE id = ?",
                   (worker, now, now, FIRST_PHASE, row["id"]))
        return _job(db.execute("SELECT * FROM jobs WHERE id = ?", (row["id"],)).fetchone())


def progress(job_id: str, worker: str, data: dict) -> None:
    with _write, _db() as db:
        db.execute("UPDATE jobs SET phase=?, queries=?, urls=?, log=?, cost_rub=?, heartbeat=? "
                   "WHERE id = ? AND worker = ? AND status = 'running'",
                   (data.get("phase"), json.dumps(data.get("queries") or [], ensure_ascii=False),
                    json.dumps(data.get("urls") or [], ensure_ascii=False), json.dumps(data.get("log") or [], ensure_ascii=False),
                    data.get("cost_rub"), time.time(), job_id, worker))


def finish(job_id: str, report_id: int, cost_rub: float | None) -> None:
    """A finished report is accepted even if the job was given up as interrupted meanwhile."""
    with _write, _db() as db:
        db.execute("UPDATE jobs SET status='done', phase='Готово', report_id=?, cost_rub=COALESCE(?, cost_rub), finished=? WHERE id = ?",
                   (report_id, cost_rub, time.time(), job_id))


def fail(job_id: str, error: str) -> None:
    with _write, _db() as db:
        db.execute("UPDATE jobs SET status='error', phase='Остановлено', error=?, finished=? WHERE id = ?",
                   (error, time.time(), job_id))

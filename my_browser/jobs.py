"""Research job queue shared by the web app and the Telegram bot.

Each job runs in its own thread, one at a time (pipeline env overrides are process-wide).
Progress comes from pipeline phases plus gpt-researcher's "scraper" log, which carries the
planned search queries and every accepted source; the UIs poll the Job objects.
"""

import ast
import asyncio
import logging
import re
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass, field

import gpt_researcher.actions  # noqa: F401  creates the "scraper" logger's console handler first

from my_browser.pipeline import PreflightError, env_overrides, overrides, preflight, research, save

logger = logging.getLogger(__name__)

RESEARCH_LOCK = threading.Lock()
MAX_KEPT_JOBS = 50


@dataclass
class Job:
    query: str
    mode: str
    sources: str
    origin: str = "web"  # web | telegram
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:10])
    status: str = "queued"  # queued | running | done | error
    phase: str = "В очереди"
    created: float = field(default_factory=time.time)
    started: float | None = None
    finished: float | None = None
    queries: list[str] = field(default_factory=list)
    urls: list[tuple[str, str]] = field(default_factory=list)  # (url, score like "3/3")
    log: deque = field(default_factory=lambda: deque(maxlen=300))
    log_count: int = 0  # total lines ever logged, so pollers can tell which deque entries are new
    report_id: int | None = None
    error: str | None = None

    @property
    def active(self) -> bool:
        return self.status in ("queued", "running")

    @property
    def elapsed(self) -> float:
        if not self.started:
            return 0.0
        return (self.finished or time.time()) - self.started


JOBS: dict[str, Job] = {}
_current: Job | None = None

QUERIES_LINE = re.compile(r"based on the following queries: (\[.*\])")
SOURCE_LINE = re.compile(r"Added source url to research(?: \(([^)]*)\))?: (\S+)")


class _LogToJob(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        job = _current
        if job is None:
            return
        message = record.getMessage().strip()
        job.log.append(message)
        job.log_count += 1
        if match := QUERIES_LINE.search(message):
            try:
                job.queries += [q for q in ast.literal_eval(match.group(1)) if q not in job.queries]
            except (ValueError, SyntaxError):
                pass
        elif match := SOURCE_LINE.search(message):
            job.urls.append((match.group(2), match.group(1) or ""))


logging.getLogger("scraper").addHandler(_LogToJob())


def submit(query: str, mode: str, sources: str, origin: str = "web") -> Job:
    job = Job(query=query, mode=mode, sources=sources, origin=origin)
    JOBS[job.id] = job
    for old in sorted((j for j in JOBS.values() if not j.active), key=lambda j: j.created)[:-MAX_KEPT_JOBS]:
        JOBS.pop(old.id, None)
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"research-{job.id}").start()
    return job


def active() -> list[Job]:
    return sorted((j for j in JOBS.values() if j.active), key=lambda j: j.created)


def busy() -> bool:
    return RESEARCH_LOCK.locked()


def _run(job: Job) -> None:
    global _current
    with RESEARCH_LOCK:
        _current = job
        job.status, job.started, job.phase = "running", time.time(), "Проверяю DeepSeek и поиск..."
        try:
            with env_overrides(overrides(job.sources)):
                preflight()
                started = time.monotonic()
                markdown, urls = asyncio.run(research(job.query, job.mode, on_progress=lambda text: setattr(job, "phase", text)))
                job.phase = "Собираю PDF и DOCX..."
                report = save(job.query, job.mode, markdown, urls, time.monotonic() - started)
            job.report_id, job.status, job.phase = report.id, "done", "Готово"
        except PreflightError as e:
            job.status, job.error, job.phase = "error", str(e), "Остановлено"
        except Exception as e:
            logger.exception("Research failed: %s", job.query)
            job.status, job.error, job.phase = "error", str(e)[:500] or type(e).__name__, "Остановлено"
        finally:
            job.finished = time.time()
            _current = None

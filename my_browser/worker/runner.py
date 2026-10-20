"""The worker's job loop: take a research from core's queue, run it, report progress, hand in the files.

One research at a time, in a plain thread (the all-in-one process starts it next to core; on its own
the worker starts it next to its API). Progress (phase, planned queries, accepted sources with
scores, log tail, cost) comes from pipeline phases and gpt-researcher's "scraper" log, goes to core
every few seconds and doubles as the heartbeat core uses to spot interrupted jobs. A job with
"factcheck" has its report checked against the pages it read before the files are rendered.
"""

import ast
import asyncio
import base64
import logging
import re
import threading
import time
import uuid
from collections import deque

import gpt_researcher.actions  # noqa: F401  creates the "scraper" logger's console handler first

from my_browser.common import costs, phases
from my_browser.common.clients import call_core
from my_browser.worker import factcheck, hooks, suggest
from my_browser.worker.pipeline import PreflightError, config_file, preflight, render, research, run_settings

logger = logging.getLogger(__name__)

WORKER_ID = uuid.uuid4().hex[:8]  # a restarted worker tells core to give up the old one's jobs
QUERIES_LINE = re.compile(r"based on the following queries: (\[.*\])")
SOURCE_LINE = re.compile(r"Added source url to research(?: \(([^)]*)\))?: (\S+)")
TICK_SEC = 3
HEARTBEAT_SEC = 30


class Progress(logging.Handler):
    """What the job page shows, collected from pipeline phases and the "scraper" log."""

    def __init__(self) -> None:
        super().__init__()
        self.phase = phases.PREFLIGHT
        self.queries: list[str] = []
        self.urls: list[list[str]] = []  # [url, score like "3/3"]
        self.log: deque = deque(maxlen=200)
        self.version = 0  # bumped on every change, so unchanged progress is not resent

    def set_phase(self, text: str) -> None:
        self.phase = text
        self.version += 1

    def emit(self, record: logging.LogRecord) -> None:
        message = record.getMessage().strip()
        self.log.append(message)
        if match := QUERIES_LINE.search(message):
            try:
                self.queries += [q for q in ast.literal_eval(match.group(1)) if q not in self.queries]
            except (ValueError, SyntaxError):
                pass
        elif match := SOURCE_LINE.search(message):
            self.urls.append([match.group(2), match.group(1) or ""])
        self.version += 1

    def snapshot(self, meter: costs.Meter) -> dict:
        return {"worker": WORKER_ID, "phase": self.phase, "queries": self.queries, "urls": self.urls,
                "log": list(self.log), "cost_rub": round(meter.rub(), 2)}


def _send_progress(job_id: str, progress: Progress, meter: costs.Meter, stop: threading.Event) -> None:
    sent, sent_at = -1, 0.0
    while not stop.wait(TICK_SEC):
        if progress.version != sent or time.monotonic() - sent_at > HEARTBEAT_SEC:
            try:
                call_core("POST", f"/api/worker/jobs/{job_id}/progress", json=progress.snapshot(meter))
                sent, sent_at = progress.version, time.monotonic()
            except Exception as e:
                logger.warning("Progress not sent: %s", e)


def _hand_in(job_id: str, files: dict[str, bytes]) -> None:
    payload = {"worker": WORKER_ID, "files": {suffix: base64.b64encode(data).decode() for suffix, data in files.items()}}
    for attempt in range(6):  # core may be restarting
        try:
            call_core("POST", f"/api/worker/jobs/{job_id}/result", json=payload, timeout=120)
            return
        except Exception as e:
            logger.warning("Report not handed in (attempt %d): %s", attempt + 1, e)
            time.sleep(5 * (attempt + 1))
    raise RuntimeError("core did not accept the report")


def _fail(job_id: str, error: str) -> None:
    try:
        call_core("POST", f"/api/worker/jobs/{job_id}/error", json={"worker": WORKER_ID, "error": error})
    except Exception as e:
        logger.warning("Error not reported for %s: %s", job_id, e)


def execute(job: dict) -> None:
    progress, meter, stop = Progress(), costs.Meter(), threading.Event()
    scraper_log = logging.getLogger("scraper")
    scraper_log.addHandler(progress)
    ticker = threading.Thread(target=_send_progress, args=(job["id"], progress, meter, stop), daemon=True)
    ticker.start()
    metering = costs.current.set(meter)  # asyncio.run copies the context (meter, blocklist) into the research
    blocking = hooks.blocked.set(job.get("blocked") or [])
    try:
        settings = run_settings(job["sources"], writer=job["writer"])
        preflight(settings)
        started, check = time.monotonic(), None
        with config_file(settings) as path:
            result = asyncio.run(research(job["query"], job["mode"], path, on_progress=progress.set_phase))
            markdown = result.markdown
            if job.get("factcheck"):
                markdown, check = asyncio.run(factcheck.run(markdown, result.pages, path, on_progress=progress.set_phase))
        seconds = time.monotonic() - started
        progress.set_phase(phases.FOLLOWUPS)
        followups = asyncio.run(suggest.followups(job["query"], markdown))
        progress.set_phase(phases.FILES)
        files = render(job["query"], job["mode"], markdown, result.sources, seconds, settings, cost=meter.summary(),
                       followups=followups, parent=job.get("parent"), factcheck=check)
        _hand_in(job["id"], files)
    except PreflightError as e:
        _fail(job["id"], str(e))
    except Exception as e:
        logger.exception("Research failed: %s", job["query"])
        _fail(job["id"], str(e)[:500] or type(e).__name__)
    finally:
        stop.set()
        ticker.join(timeout=5)
        scraper_log.removeHandler(progress)
        costs.current.reset(metering)
        hooks.blocked.reset(blocking)


def run_forever(stop: threading.Event | None = None) -> None:
    """Take jobs from core one at a time until stopped; meant for its own thread."""
    stop = stop or threading.Event()
    costs.usd_rub()  # fetch the exchange rate before the first job
    greeted = False
    while not stop.is_set():
        try:
            if not greeted:
                call_core("POST", "/api/worker/hello", json={"worker": WORKER_ID})
                greeted = True
                logger.info("Job loop ready: worker %s takes researches from core", WORKER_ID)
            response = call_core("POST", "/api/worker/claim", json={"worker": WORKER_ID})
        except Exception as e:  # core is starting or restarting
            logger.info("Core unreachable: %s", e)
            stop.wait(5)
            continue
        if response.status_code == 204:
            stop.wait(2)
            continue
        execute(response.json())

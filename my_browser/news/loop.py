"""The news loop: a plain thread that wakes every minute and does what is due.

Each tick polls the sources whose interval has passed and stores their new posts; every few minutes it merges
the new posts into stories (dedup.py) and has the LLM score them (scoring.py); every tick it queues what is due
for Telegram (delivery.py: urgent stories, digests). One loop per REPORTS_DIR: the all-in-one app.py starts it next to the
worker's job loop (NEWS_LOOP=0 turns it off), `python -m my_browser.news` next to its API. Every tick is marked
in the state table, so /status can tell whether a loop runs.
"""

import asyncio
import logging
import threading
import time

import httpx

from my_browser.news import collectors, config, db, dedup, delivery, items, scoring, sources

logger = logging.getLogger(__name__)

TICK_SEC = 60
CONCURRENCY = 6
HOST_GAP_SEC = 2.0  # between two requests to one host: Reddit answers 429 to quicker ones
TIMEOUT = httpx.Timeout(25, connect=10)
# A browser's User-Agent: every starting source answered it on 24.10.2026, some refuse unknown clients.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/605.1.15 (KHTML, like Gecko) "
              "Version/17.0 Safari/605.1.15")
SLIM_EVERY_SEC = 86400
DEDUP_EVERY_SEC = 300  # an embedding run starts a child process and loads the model: a few seconds

_collecting = threading.Lock()  # the loop and POST /collect never poll at once


async def collect(everything: bool = False) -> dict:
    """Poll the due sources (every enabled one with `everything`) and store their posts; a summary of the run."""
    if not _collecting.acquire(blocking=False):
        return {"busy": True}
    try:
        return await _collect(everything)
    finally:
        _collecting.release()


async def _collect(everything: bool) -> dict:
    started = time.monotonic()
    window = config.load().dedup_window_hours
    todo = sources.enabled() if everything else sources.due()
    semaphore, host_locks = asyncio.Semaphore(CONCURRENCY), {}
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT, follow_redirects=True) as client:
        results = await asyncio.gather(*(
            _poll(source, client, semaphore, host_locks.setdefault(collectors.host(source["url"]), asyncio.Lock()), window)
            for source in todo))
    done = [r for r in results if r is not None]
    summary = {"at": time.time(), "polled": len(todo), "new": sum(r[0] for r in done),
               "inserted": sum(r[1] for r in done), "failed": len(results) - len(done),
               "seconds": round(time.monotonic() - started, 1)}
    if todo:
        db.set_state("last_collect", summary)
    return summary


async def _poll(source: dict, client: httpx.AsyncClient, semaphore: asyncio.Semaphore, host_lock: asyncio.Lock,
                window: int) -> tuple[int, int] | None:
    """(fresh, inserted) posts of one source, or None when it failed; one request per host at a time."""
    async with host_lock:
        async with semaphore:
            try:
                fetched = await collectors.fetch(source, client)
                result = items.store(source, fetched.posts, window)
                sources.mark_ok(source["id"], fetched.etag, fetched.modified)
            except Exception as e:  # one broken source must not stop the others
                error = _describe(e)
                logger.warning("News source failed: %s (%s)", source["name"], error)
                sources.mark_failed(source["id"], error)
                result = None
        await asyncio.sleep(HOST_GAP_SEC)
    return result


def _describe(error: Exception) -> str:
    if isinstance(error, httpx.HTTPStatusError):
        return f"HTTP {error.response.status_code}"
    if isinstance(error, httpx.TimeoutException):
        return "timeout"
    return f"{type(error).__name__}: {error}"[:200]


def tick() -> None:
    now = time.time()
    db.set_state("loop_tick", now)
    if sources.due(now):
        summary = asyncio.run(collect())
        if summary.get("new") or summary.get("failed"):
            logger.info("News: %(polled)d sources polled, %(new)d new posts, %(failed)d failed, %(seconds)s s", summary)
    if now - (db.get_state("dedup_at") or 0) >= DEDUP_EVERY_SEC:
        db.set_state("dedup_at", now)  # before the run: a failing model does not retry every minute
        try:
            dedup.run()
        except Exception:
            logger.exception("News loop: merging failed")
        asyncio.run(scoring.run())  # stories merged before still get scored when merging failed
    delivery.run()
    if now - (db.get_state("slimmed_at") or 0) > SLIM_EVERY_SEC:
        items.slim()
        db.set_state("slimmed_at", now)


def run_forever(stop: threading.Event | None = None) -> None:
    """Tick until stopped; meant for its own thread."""
    stop = stop or threading.Event()
    if added := sources.seed():
        logger.info("News: %d starting sources added", added)
    logger.info("News loop ready")
    while not stop.is_set():
        try:
            tick()
        except Exception:
            logger.exception("News loop: tick failed")
        stop.wait(TICK_SEC)

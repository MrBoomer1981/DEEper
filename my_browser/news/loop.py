"""The news loop: a plain thread that wakes every minute and does what is due.

One loop per REPORTS_DIR: the all-in-one app.py starts it next to the worker's job loop (NEWS_LOOP=0 turns it
off), `python -m my_browser.news` next to its API. Every tick is marked in the state table, so /status can tell
whether a loop runs.
"""

import logging
import threading
import time

from my_browser.news import db, sources

logger = logging.getLogger(__name__)

TICK_SEC = 60


def tick() -> None:
    db.set_state("loop_tick", time.time())


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

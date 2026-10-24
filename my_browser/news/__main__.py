"""Run the news part as its own process: its API (NEWS_PORT, default 8504) and the news loop."""

import os
import threading

import uvicorn

from my_browser.common.settings import configure_logging
from my_browser.news import loop
from my_browser.news.api import app

if __name__ == "__main__":
    configure_logging()
    threading.Thread(target=loop.run_forever, daemon=True, name="news-loop").start()
    uvicorn.run(app, host=os.environ.get("NEWS_HOST", "127.0.0.1"), port=int(os.environ.get("NEWS_PORT", 8504)))

"""DEEper in one process: core's web app and API, the worker's job loop, the news loop and the Telegram bot.

    uv run app.py   -> http://127.0.0.1:8501 (on Railway: PORT, public, APP_PASSWORD required)

The worker's, search's and news's APIs are called in-process (no WORKER_URL / SEARCH_URL / NEWS_URL); the
job loop and the bot reach core over localhost. Each part also runs on its own: python -m my_browser.<part>.
"""

import asyncio
import logging
import os
import threading

# NiceGUI comes only through core.web: it must first point NiceGUI's storage (logins) at the reports
# volume, and NiceGUI reads that path when it is imported.
from my_browser.bot import telegram
from my_browser.core import web
from my_browser.news import loop as news_loop
from my_browser.worker import runner

background: set[asyncio.Task] = set()


async def start_parts() -> None:
    threading.Thread(target=runner.run_forever, daemon=True, name="job-loop").start()
    if os.environ.get("NEWS_LOOP", "1") != "0":
        threading.Thread(target=news_loop.run_forever, daemon=True, name="news-loop").start()
    if telegram.token():
        task = asyncio.create_task(telegram.start(handle_signals=False))
        background.add(task)
        task.add_done_callback(lambda t: t.cancelled() or not t.exception() or logging.error("Bot stopped: %r", t.exception()))


web.app.on_startup(start_parts)

if __name__ in {"__main__", "__mp_main__"}:
    web.main()

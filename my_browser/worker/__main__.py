"""Run the worker part as its own process: its API (WORKER_PORT, default 8502) and the job loop."""

import os
import threading

import uvicorn

from my_browser.common.settings import configure_logging
from my_browser.worker import runner
from my_browser.worker.api import app

if __name__ == "__main__":
    configure_logging()
    threading.Thread(target=runner.run_forever, daemon=True, name="job-loop").start()
    uvicorn.run(app, host=os.environ.get("WORKER_HOST", "127.0.0.1"), port=int(os.environ.get("WORKER_PORT", 8502)))

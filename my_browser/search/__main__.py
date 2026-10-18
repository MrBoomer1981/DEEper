"""Run the search part as its own process: uv run python -m my_browser.search"""

import os

import uvicorn

from my_browser.common.settings import configure_logging
from my_browser.search.api import app

if __name__ == "__main__":
    configure_logging()
    uvicorn.run(app, host=os.environ.get("SEARCH_HOST", "127.0.0.1"), port=int(os.environ.get("SEARCH_PORT", 8503)))

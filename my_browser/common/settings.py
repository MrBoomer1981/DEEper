"""Settings shared by every part: paths, research options, and how the parts reach each other.

DEEper runs as five parts: core (web UI, API, report library, job queue), worker (research engine and
LLM helpers), search (source selection and page reading), news (the news radar and its loop) and bot
(Telegram). Each can run as its own process. Unset *_URL variables mean the all-in-one process (Railway,
`uv run app.py`): core serves HTTP, worker, search and news are called in-process through their ASGI apps,
the bot and the worker's job loop reach core over localhost.
"""

import logging
import os
import secrets
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
CONFIG_PATH = ROOT / "config.json"

load_dotenv(ROOT / ".env")

REPORTS_DIR = Path(os.environ.get("REPORTS_DIR") or ROOT / "reports")  # eval sets its own

# Web search backend: the local SearXNG when SEARX_URL is set; otherwise Tavily if its key
# is present (Railway has no SearXNG, and engines block datacenter IPs more often).
# WEB_RETRIEVER overrides the choice.
WEB_RETRIEVER = os.environ.get("WEB_RETRIEVER") or (
    "tavily" if os.environ.get("TAVILY_API_KEY") and not os.environ.get("SEARX_URL") else "searx"
)

# semantic_scholar is left out: without an API key it answers 429 most of the time.
SOURCES = {
    "web": WEB_RETRIEVER,
    "academic": "arxiv,openalex",
    "all": f"{WEB_RETRIEVER},arxiv,openalex",
}
DEFAULT_SOURCES = "all"

# Model that writes the report text (SMART_LLM); search, planning and source rating stay on FAST/STRATEGIC.
WRITERS = {"pro": "deepseek:deepseek-v4-pro", "flash": "deepseek:deepseek-flash"}
DEFAULT_WRITER = "pro"

MODES = {
    "detailed": "подробный отчёт: введение, разделы по подтемам, заключение (самый длинный)",
    "deep": "глубокое исследование: рекурсивный поиск вширь и вглубь, затем один отчёт",
    "quick": "обычный отчёт за один проход (быстрее и дешевле)",
}
# Modes whose reports are fact-checked unless the user switches it off (worker/factcheck.py).
FACTCHECK_MODES = ("detailed", "deep")

PORT = int(os.environ.get("PORT", 8501))  # 8080 is the local SearXNG
CORE_URL = os.environ.get("CORE_URL") or f"http://127.0.0.1:{PORT}"
WORKER_URL = os.environ.get("WORKER_URL")  # unset: in-process
SEARCH_URL = os.environ.get("SEARCH_URL")  # unset: in-process
NEWS_URL = os.environ.get("NEWS_URL")  # unset: in-process


def service_token() -> str:
    """Shared secret of the parts' APIs: SERVICE_TOKEN, else a file next to the reports (one machine)."""
    if token := os.environ.get("SERVICE_TOKEN"):
        return token
    path = REPORTS_DIR / ".service_token"
    if not path.exists():
        REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_hex(24))
    return path.read_text().strip()


def configure_logging() -> None:
    """INFO for our parts; httpx logs every request at INFO, and the worker polls core every few seconds."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

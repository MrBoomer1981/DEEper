"""Research pipeline shared by the CLI and the Telegram bot: checks, run, save."""

import json
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = ROOT / "config.json"
REPORTS_DIR = ROOT / "reports"

load_dotenv(ROOT / ".env")

# semantic_scholar is left out: without an API key it answers 429 most of the time.
SOURCES = {
    "web": "searx",
    "academic": "arxiv,openalex",
    "all": "searx,arxiv,openalex",
}

MODES = {
    "detailed": "подробный отчёт: введение, разделы по подтемам, заключение (самый длинный)",
    "deep": "глубокое исследование: рекурсивный поиск вширь и вглубь, затем один отчёт",
    "quick": "обычный отчёт за один проход (быстрее и дешевле)",
}


class PreflightError(Exception):
    pass


@dataclass
class Report:
    id: int | None
    query: str
    mode: str
    md_path: Path
    words: int
    seconds: float
    sources: list[str] = field(default_factory=list)


def overrides(sources: str | None = None, words: int | None = None, lang: str | None = None) -> dict[str, str]:
    """GPT Researcher reads env vars with priority over config.json, so per-run options go there."""
    values = {}
    if sources:
        values["RETRIEVER"] = SOURCES[sources]
    if words:
        values["TOTAL_WORDS"] = str(words)
    if lang:
        values["LANGUAGE"] = lang
    return values


@contextmanager
def env_overrides(values: dict[str, str]):
    """Process-wide, so callers must not run two researches at once (the bot serializes them)."""
    saved = {key: os.environ.get(key) for key in values}
    os.environ.update(values)
    try:
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _config() -> dict:
    return json.loads(CONFIG_PATH.read_text("utf-8"))


def check_deepseek(model: str, extra_body: dict) -> None:
    """One-token request so a bad key or empty balance fails in seconds, not mid-report."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        raise PreflightError("Нет DEEPSEEK_API_KEY. Создайте ключ на https://platform.deepseek.com/api_keys и впишите его в .env")
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], **extra_body, "max_tokens": 1},
            timeout=30,
        )
    except requests.RequestException as e:
        raise PreflightError(f"DeepSeek API недоступен: {e}") from e
    if r.status_code == 401:
        raise PreflightError("DeepSeek отклонил ключ (401). Проверьте DEEPSEEK_API_KEY в .env")
    if r.status_code == 402:
        raise PreflightError("На счёте DeepSeek нет средств (402). Пополните баланс: https://platform.deepseek.com/top_up")
    if not r.ok:
        raise PreflightError(f"DeepSeek ответил {r.status_code}: {r.text[:300]}")


def preflight() -> None:
    config = _config()
    llms = [os.environ.get(k) or config.get(k, "") for k in ("FAST_LLM", "SMART_LLM", "STRATEGIC_LLM")]
    deepseek_models = sorted({llm.split(":", 1)[1] for llm in llms if llm.startswith("deepseek:")})
    llm_kwargs = json.loads(os.environ["LLM_KWARGS"]) if os.environ.get("LLM_KWARGS") else config.get("LLM_KWARGS", {})
    for model in deepseek_models:
        check_deepseek(model, llm_kwargs.get("extra_body", {}))

    retrievers = os.environ.get("RETRIEVER") or config.get("RETRIEVER", "")
    if "searx" in retrievers.split(","):
        url = os.environ.get("SEARX_URL", "")
        try:
            requests.get(f"{url}/search", params={"q": "test", "format": "json"}, timeout=10).raise_for_status()
        except requests.RequestException as e:
            raise PreflightError(f"SearXNG не отвечает по {url!r} ({e}). Запустите: docker compose up -d") from e


async def research(query: str, mode: str, on_progress: Callable[[str], None] = print) -> tuple[str, list[str]]:
    from gpt_researcher import GPTResearcher

    from my_browser.detailed import DetailedReport

    if mode == "detailed":
        report = DetailedReport(query, config_path=str(CONFIG_PATH), on_progress=on_progress)
        return await report.run(), report.source_urls()

    report_type = "deep" if mode == "deep" else "research_report"
    researcher = GPTResearcher(query=query, report_type=report_type, config_path=str(CONFIG_PATH))
    on_progress("Ищу и читаю источники...")
    if mode == "deep":
        await researcher.conduct_research(on_progress=lambda p: on_progress(
            f"Глубина {p.current_depth}/{p.total_depth}, запросов {p.completed_queries}/{p.total_queries}"
        ))
    else:
        await researcher.conduct_research()
    on_progress("Пишу отчёт...")
    return await researcher.write_report(), researcher.get_source_urls()


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^\w]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "report"


def save(query: str, mode: str, markdown: str, sources: list[str], seconds: float, pdf: bool = True) -> Report:
    """Write md/json/docx/pdf to reports/ and index the report in the library."""
    from my_browser.export import write_docx, write_pdf
    from my_browser.library import Library

    REPORTS_DIR.mkdir(exist_ok=True)
    base = REPORTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M%S}_{slugify(query)}"
    md_path = base.with_suffix(".md")
    md_path.write_text(markdown, "utf-8")
    base.with_suffix(".json").write_text(json.dumps({
        "query": query,
        "mode": mode,
        "retriever": os.environ.get("RETRIEVER") or _config().get("RETRIEVER"),
        "created": datetime.now().isoformat(timespec="seconds"),
        "duration_sec": round(seconds),
        "words": len(markdown.split()),
        "sources": sources,
    }, ensure_ascii=False, indent=2), "utf-8")

    try:
        write_docx(markdown, base.with_suffix(".docx"))
    except Exception as e:
        print(f"DOCX не собран: {e}")
    if pdf:
        try:
            write_pdf(md_path, base.with_suffix(".pdf"))
        except Exception as e:
            print(f"PDF не собран: {e}")

    report_id = Library().add(base.with_suffix(".json"))
    return Report(report_id, query, mode, md_path, len(markdown.split()), seconds, sources)

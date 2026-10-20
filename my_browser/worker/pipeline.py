"""The research engine: checks, one research run, and the report's files.

Used by the worker's job loop, research.py and eval. Each run gets its own config file
(config.json plus the run's choices), so nothing process-wide changes during a research;
environment variables still win over it, as everywhere in gpt-researcher.
"""

import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Callable, NamedTuple

import requests

from my_browser.common import phases
from my_browser.common.report import section
from my_browser.common.settings import CONFIG_PATH, SOURCES, WRITERS


class PreflightError(Exception):
    pass


class Research(NamedTuple):
    markdown: str
    sources: list[str]
    pages: dict[str, dict]  # url -> {"text", "title"} of every page read, for the fact-check


def run_settings(sources: str | None = None, writer: str | None = None, words: int | None = None,
                 lang: str | None = None) -> dict:
    """config.json with this run's choices of sources, writer model, length and language."""
    settings = json.loads(CONFIG_PATH.read_text("utf-8"))
    if sources:
        settings["RETRIEVER"] = SOURCES[sources]
    if writer:
        settings["SMART_LLM"] = WRITERS[writer]
    if words:
        settings["TOTAL_WORDS"] = words
    if lang:
        settings["LANGUAGE"] = lang
    return settings


def effective(settings: dict, key: str):
    """What gpt-researcher will use: an environment variable beats the config file."""
    return os.environ.get(key) or settings.get(key, "")


@contextmanager
def config_file(settings: dict):
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False)
    try:
        yield Path(f.name)
    finally:
        Path(f.name).unlink(missing_ok=True)


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


def preflight(settings: dict) -> None:
    llms = [effective(settings, k) for k in ("FAST_LLM", "SMART_LLM", "STRATEGIC_LLM")]
    deepseek_models = sorted({llm.split(":", 1)[1] for llm in llms if llm.startswith("deepseek:")})
    llm_kwargs = json.loads(os.environ["LLM_KWARGS"]) if os.environ.get("LLM_KWARGS") else settings.get("LLM_KWARGS", {})
    for model in deepseek_models:
        check_deepseek(model, llm_kwargs.get("extra_body", {}))

    retrievers = effective(settings, "RETRIEVER")
    if "tavily" in retrievers.split(",") and not os.environ.get("TAVILY_API_KEY"):
        raise PreflightError("Нет TAVILY_API_KEY: он нужен для поиска через Tavily (https://app.tavily.com)")
    if "searx" in retrievers.split(","):
        url = os.environ.get("SEARX_URL", "")
        try:
            r = requests.get(f"{url}/search", params={"q": "test", "format": "json"}, timeout=10, allow_redirects=False)
            r.raise_for_status()
            if "results" not in r.json():
                raise ValueError("no results field")
        except (requests.RequestException, ValueError) as e:
            # ValueError also covers a non-JSON answer: something else is listening on that port.
            raise PreflightError(f"SearXNG не отвечает по {url!r} ({e}). Запустите Docker и: docker compose up -d") from e


async def research(query: str, mode: str, config_path: Path, on_progress: Callable[[str], None] = print) -> Research:
    from gpt_researcher import GPTResearcher

    from my_browser.common import costs  # noqa: F401  patch gpt-researcher (see each module)
    from my_browser.worker import hooks, prompts, stemming  # noqa: F401
    from my_browser.worker.detailed import DetailedReport

    pages: dict[str, dict] = {}
    hooks.read_pages.set(pages)  # the tasks gpt-researcher starts from here share this dict
    if mode == "detailed":
        report = DetailedReport(query, config_path=str(config_path), on_progress=on_progress)
        return Research(await report.run(), report.source_urls(), pages)

    report_type = "deep" if mode == "deep" else "research_report"
    researcher = GPTResearcher(query=query, report_type=report_type, config_path=str(config_path))
    on_progress(phases.READING)
    if mode == "deep":
        await researcher.conduct_research(on_progress=lambda p: on_progress(
            phases.depth(p.current_depth, p.total_depth, p.completed_queries, p.total_queries)
        ))
    else:
        await researcher.conduct_research()
    on_progress(phases.WRITING)
    return Research(await researcher.write_report(), researcher.get_source_urls(), pages)


def render(query: str, mode: str, markdown: str, sources: list[str], seconds: float, settings: dict,
           cost: dict | None = None, followups: list[dict] | None = None, parent: str | None = None,
           factcheck: dict | None = None, pdf: bool = True) -> dict[str, bytes]:
    """The report's files keyed by suffix (.md, .json, .docx, .pdf); core stores them.

    The fact-check's summary and the follow-up topics go into the JSON and, as the last two
    sections, into the documents. parent is the file name (without suffix) of the report this
    research continues.
    """
    from my_browser.worker import factcheck as checks
    from my_browser.worker.export import write_docx, write_pdf

    full = markdown + checks.section(factcheck) + section(followups or [])
    meta = {
        "query": query,
        "mode": mode,
        "retriever": effective(settings, "RETRIEVER"),
        "writer": effective(settings, "SMART_LLM").split(":", 1)[-1],
        "cost": cost,
        "created": datetime.now().isoformat(timespec="seconds"),
        "duration_sec": round(seconds),
        "words": len(markdown.split()),
        "sources": sources,
        "followups": followups or [],
        "parent": parent,
    }
    if factcheck is not None:
        meta["factcheck"] = factcheck
    files = {".md": full.encode(), ".json": json.dumps(meta, ensure_ascii=False, indent=2).encode()}
    with tempfile.TemporaryDirectory() as tmp:
        md_path = Path(tmp) / "report.md"
        md_path.write_text(full, "utf-8")
        try:
            write_docx(full, md_path.with_suffix(".docx"))
            files[".docx"] = md_path.with_suffix(".docx").read_bytes()
        except Exception as e:
            print(f"DOCX не собран: {e}")
        if pdf:
            try:
                write_pdf(md_path, md_path.with_suffix(".pdf"))
                files[".pdf"] = md_path.with_suffix(".pdf").read_bytes()
            except Exception as e:
                print(f"PDF не собран: {e}")
    return files

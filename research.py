"""Run a research report and save it to reports/ as Markdown, DOCX and PDF.

Examples:
    uv run research.py "тема исследования"
    uv run research.py "тема" --mode deep --sources all --words 2500
"""

import argparse
import asyncio
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Исследование темы с отчётом в reports/")
    parser.add_argument("query", help="тема или вопрос")
    parser.add_argument("--mode", choices=MODES, default="detailed",
                        help="; ".join(f"{k} — {v}" for k, v in MODES.items()))
    parser.add_argument("--sources", choices=SOURCES,
                        help="web — SearXNG; academic — arXiv и OpenAlex; all — всё сразу "
                             "(по умолчанию RETRIEVER из config.json)")
    parser.add_argument("--words", type=int, help="минимальная длина отчёта/раздела в словах (TOTAL_WORDS)")
    parser.add_argument("--lang", help="язык отчёта, например russian или english (LANGUAGE)")
    parser.add_argument("--no-pdf", action="store_true", help="не собирать PDF")
    return parser.parse_args()


def apply_overrides(args: argparse.Namespace) -> None:
    # GPT Researcher reads env vars with priority over config.json.
    if args.sources:
        os.environ["RETRIEVER"] = SOURCES[args.sources]
    if args.words:
        os.environ["TOTAL_WORDS"] = str(args.words)
    if args.lang:
        os.environ["LANGUAGE"] = args.lang


def check_deepseek(model: str, extra_body: dict) -> None:
    """One-token request so a bad key or empty balance fails in seconds, not mid-report."""
    key = os.environ.get("DEEPSEEK_API_KEY")
    if not key:
        sys.exit("Нет DEEPSEEK_API_KEY. Создайте ключ на https://platform.deepseek.com/api_keys и впишите его в .env")
    try:
        r = requests.post(
            "https://api.deepseek.com/chat/completions",
            headers={"Authorization": f"Bearer {key}"},
            json={"model": model, "messages": [{"role": "user", "content": "ping"}], **extra_body, "max_tokens": 1},
            timeout=30,
        )
    except requests.RequestException as e:
        sys.exit(f"DeepSeek API недоступен: {e}")
    if r.status_code == 401:
        sys.exit("DeepSeek отклонил ключ (401). Проверьте DEEPSEEK_API_KEY в .env")
    if r.status_code == 402:
        sys.exit("На счёте DeepSeek нет средств (402). Пополните баланс: https://platform.deepseek.com/top_up")
    if not r.ok:
        sys.exit(f"DeepSeek ответил {r.status_code}: {r.text[:300]}")


def preflight() -> None:
    config = json.loads(CONFIG_PATH.read_text("utf-8"))
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
            sys.exit(f"SearXNG не отвечает по {url!r} ({e}). Запустите: docker compose up -d")


async def run(query: str, mode: str) -> tuple[str, list[str]]:
    from gpt_researcher import GPTResearcher

    from my_browser.detailed import DetailedReport

    if mode == "detailed":
        report = DetailedReport(query, config_path=str(CONFIG_PATH))
        return await report.run(), report.source_urls()

    report_type = "deep" if mode == "deep" else "research_report"
    researcher = GPTResearcher(query=query, report_type=report_type, config_path=str(CONFIG_PATH))
    await researcher.conduct_research()
    return await researcher.write_report(), researcher.get_source_urls()


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^\w]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "report"


def save(query: str, mode: str, markdown: str, sources: list[str], seconds: float, pdf: bool) -> Path:
    from my_browser.export import write_docx, write_pdf

    REPORTS_DIR.mkdir(exist_ok=True)
    base = REPORTS_DIR / f"{datetime.now():%Y-%m-%d_%H%M}_{slugify(query)}"
    md_path = base.with_suffix(".md")
    md_path.write_text(markdown, "utf-8")
    base.with_suffix(".json").write_text(json.dumps({
        "query": query,
        "mode": mode,
        "retriever": os.environ.get("RETRIEVER") or json.loads(CONFIG_PATH.read_text("utf-8")).get("RETRIEVER"),
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
    return md_path


def main() -> None:
    args = parse_args()
    apply_overrides(args)
    preflight()

    print(f"Режим: {args.mode} — {MODES[args.mode]}\nТема: {args.query}\n")
    started = time.monotonic()
    try:
        markdown, sources = asyncio.run(run(args.query, args.mode))
    except KeyboardInterrupt:
        sys.exit(f"\nПрервано через {(time.monotonic() - started) / 60:.1f} мин, отчёт не сохранён.")
    elapsed = time.monotonic() - started

    md_path = save(args.query, args.mode, markdown, sources, elapsed, pdf=not args.no_pdf)
    print(f"\nГотово за {elapsed / 60:.1f} мин: {len(markdown.split())} слов, {len(sources)} источников")
    print(f"Отчёт: {md_path.relative_to(ROOT)} (+ .docx, .pdf, .json рядом)")


if __name__ == "__main__":
    main()

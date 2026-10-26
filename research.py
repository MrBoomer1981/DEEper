"""Run a research report and save it to reports/ as Markdown, DOCX and PDF (no server needed).

Examples:
    uv run research.py "тема исследования"           # обзор на 5–7 минут чтения
    uv run research.py "тема" --mode detailed         # подробный отчёт, до 5000 слов
    uv run research.py "тема" --mode deep --sources all --words 2500
"""

import argparse
import asyncio
import sys
import time

from my_browser.common import costs
from my_browser.common.report import KINDS, reading_words
from my_browser.common.settings import (DEFAULT_MODE, DEFAULT_SOURCES, DEFAULT_WRITER, FACTCHECK_MODES, MODES, ROOT, SOURCES,
                                        WRITERS)
from my_browser.common.text import reading_minutes, rub_text
from my_browser.core import blocklist
from my_browser.core.library import Library
from my_browser.worker import factcheck, hooks, suggest
from my_browser.worker.pipeline import PreflightError, config_file, preflight, render, research, run_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Исследование темы с отчётом в reports/")
    parser.add_argument("query", help="тема или вопрос")
    parser.add_argument("--mode", choices=MODES, default=DEFAULT_MODE,
                        help="; ".join(f"{k} — {v}" for k, v in MODES.items()))
    parser.add_argument("--sources", choices=SOURCES, default=DEFAULT_SOURCES,
                        help="web — веб-поиск (SearXNG или Tavily); academic — arXiv и OpenAlex; "
                             "all — всё сразу (по умолчанию)")
    parser.add_argument("--writer", choices=WRITERS, default=DEFAULT_WRITER,
                        help="модель для итогового текста: pro — DeepSeek V4 Pro (лучше, дороже ≈3×), flash — DeepSeek Flash")
    parser.add_argument("--words", type=int, help="минимальная длина глубокого отчёта в словах (TOTAL_WORDS); обзор — 800–1 300 "
                        "слов, любой отчёт не длиннее 5000, подробный делит их между разделами сам")
    parser.add_argument("--lang", help="язык отчёта, например russian или english (LANGUAGE)")
    parser.add_argument("--factcheck", action=argparse.BooleanOptionalAction,
                        help="сверить утверждения со ссылками с источниками и исправить расхождения "
                             f"(по умолчанию — в режимах {', '.join(FACTCHECK_MODES)})")
    parser.add_argument("--no-pdf", action="store_true", help="не собирать PDF")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    meter = costs.Meter()
    costs.current.set(meter)
    hooks.blocked.set(blocklist.blocked_domains())
    settings = run_settings(args.sources, args.writer, args.words, args.lang)
    try:
        preflight(settings)
    except PreflightError as e:
        sys.exit(str(e))

    print(f"Режим: {args.mode} — {MODES[args.mode]}\nТема: {args.query}\n")
    started, check = time.monotonic(), None
    try:
        with config_file(settings) as path:
            result = asyncio.run(research(args.query, args.mode, path))
            markdown = result.markdown
            if args.factcheck if args.factcheck is not None else args.mode in FACTCHECK_MODES:
                markdown, check = asyncio.run(factcheck.run(markdown, result.pages, path, on_progress=print))
        elapsed = time.monotonic() - started
        found = asyncio.run(suggest.followups(args.query, markdown, make_route=True))
    except KeyboardInterrupt:
        sys.exit(f"\nПрервано через {(time.monotonic() - started) / 60:.1f} мин, отчёт не сохранён.")

    cost = meter.summary()
    files = render(args.query, args.mode, markdown, result.sources, elapsed, settings, cost=cost, followups=found["topics"],
                   factcheck=check, pdf=not args.no_pdf, route=found["route"])
    library = Library()
    entry = library.get(library.store(files))
    print(f"\nГотово за {elapsed / 60:.1f} мин: {entry.words} слов (≈ {reading_minutes(reading_words(markdown))} мин чтения), "
          f"{len(result.sources)} источников, {rub_text(cost['rub'])}")
    if check and check.get("checked"):
        print(f"Проверка фактов: подтверждено {check['supported']} из {check['checked']}, исправлено "
              f"{check['relinked'] + check['rewritten']}, сомнительных {len(check['doubtful'])} ({rub_text(check['cost_rub'])})")
    path = entry.md_path.relative_to(ROOT) if entry.md_path.is_relative_to(ROOT) else entry.md_path  # REPORTS_DIR may be elsewhere
    print(f"Отчёт #{entry.id}: {path} (+ .docx, .pdf, .json рядом)")
    if found["topics"]:
        print("\nЧто исследовать дальше:")
        for topic in found["topics"]:
            print(f"  [{KINDS[topic['kind']].lower()}] {topic['query']}" + (f" — {topic['why']}" if topic["why"] else ""))
    if found["route"]:
        print("\nМаршрут по теме:")
        for n, step in enumerate([{"query": args.query}, *found["route"]], 1):
            print(f"  {n}. {step['query']}")


if __name__ == "__main__":
    main()

"""Run a research report and save it to reports/ as Markdown, DOCX and PDF (no server needed).

Examples:
    uv run research.py "тема исследования"
    uv run research.py "тема" --mode deep --sources all --words 2500
"""

import argparse
import asyncio
import sys
import time

from my_browser.common import costs
from my_browser.common.report import KINDS
from my_browser.common.settings import DEFAULT_SOURCES, DEFAULT_WRITER, MODES, ROOT, SOURCES, WRITERS
from my_browser.common.text import rub_text
from my_browser.core import blocklist
from my_browser.core.library import Library
from my_browser.worker import hooks, suggest
from my_browser.worker.pipeline import PreflightError, config_file, preflight, render, research, run_settings


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Исследование темы с отчётом в reports/")
    parser.add_argument("query", help="тема или вопрос")
    parser.add_argument("--mode", choices=MODES, default="detailed",
                        help="; ".join(f"{k} — {v}" for k, v in MODES.items()))
    parser.add_argument("--sources", choices=SOURCES, default=DEFAULT_SOURCES,
                        help="web — веб-поиск (SearXNG или Tavily); academic — arXiv и OpenAlex; "
                             "all — всё сразу (по умолчанию)")
    parser.add_argument("--writer", choices=WRITERS, default=DEFAULT_WRITER,
                        help="модель для итогового текста: pro — DeepSeek V4 Pro (лучше, дороже ≈3×), flash — DeepSeek Flash")
    parser.add_argument("--words", type=int, help="минимальная длина отчёта/раздела в словах (TOTAL_WORDS)")
    parser.add_argument("--lang", help="язык отчёта, например russian или english (LANGUAGE)")
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
    started = time.monotonic()
    try:
        with config_file(settings) as path:
            markdown, sources = asyncio.run(research(args.query, args.mode, path))
        elapsed = time.monotonic() - started
        followups = asyncio.run(suggest.followups(args.query, markdown))
    except KeyboardInterrupt:
        sys.exit(f"\nПрервано через {(time.monotonic() - started) / 60:.1f} мин, отчёт не сохранён.")

    cost = meter.summary()
    files = render(args.query, args.mode, markdown, sources, elapsed, settings, cost=cost, followups=followups, pdf=not args.no_pdf)
    library = Library()
    entry = library.get(library.store(files))
    print(f"\nГотово за {elapsed / 60:.1f} мин: {entry.words} слов, {len(sources)} источников, {rub_text(cost['rub'])}")
    print(f"Отчёт #{entry.id}: {entry.md_path.relative_to(ROOT)} (+ .docx, .pdf, .json рядом)")
    if followups:
        print("\nЧто исследовать дальше:")
        for topic in followups:
            print(f"  [{KINDS[topic['kind']].lower()}] {topic['query']}" + (f" — {topic['why']}" if topic["why"] else ""))


if __name__ == "__main__":
    main()

"""Run a research report and save it to reports/ as Markdown, DOCX and PDF.

Examples:
    uv run research.py "тема исследования"
    uv run research.py "тема" --mode deep --sources all --words 2500
"""

import argparse
import asyncio
import sys
import time

from my_browser import costs, suggest
from my_browser.pipeline import (DEFAULT_SOURCES, DEFAULT_WRITER, MODES, ROOT, SOURCES, WRITERS, PreflightError,
                                 env_overrides, overrides, preflight, research, save)


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
    with env_overrides(overrides(args.sources, args.words, args.lang, args.writer)):
        try:
            preflight()
        except PreflightError as e:
            sys.exit(str(e))

        print(f"Режим: {args.mode} — {MODES[args.mode]}\nТема: {args.query}\n")
        started = time.monotonic()
        try:
            markdown, sources = asyncio.run(research(args.query, args.mode))
            elapsed = time.monotonic() - started
            followups = asyncio.run(suggest.followups(args.query, markdown))
        except KeyboardInterrupt:
            sys.exit(f"\nПрервано через {(time.monotonic() - started) / 60:.1f} мин, отчёт не сохранён.")

        cost = meter.summary()
        report = save(args.query, args.mode, markdown, sources, elapsed, pdf=not args.no_pdf, cost=cost, followups=followups)
    print(f"\nГотово за {elapsed / 60:.1f} мин: {report.words} слов, {len(sources)} источников, {costs.rub_text(cost['rub'])}")
    print(f"Отчёт #{report.id}: {report.md_path.relative_to(ROOT)} (+ .docx, .pdf, .json рядом)")
    if followups:
        print("\nЧто исследовать дальше:")
        for topic in followups:
            print(f"  [{suggest.KINDS[topic['kind']].lower()}] {topic['query']}" + (f" — {topic['why']}" if topic["why"] else ""))


if __name__ == "__main__":
    main()

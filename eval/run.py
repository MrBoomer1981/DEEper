"""Measure research quality on a fixed set of topics (eval/topics.txt).

    uv run eval/run.py                            # every topic: quick report, Flash writes the text
    uv run eval/run.py --topics 1,4 --label exa   # a subset, with a name for the results file
    uv run eval/run.py --topics 1 --mode detailed --writer pro

Every topic runs through the same pipeline as the app. Reports go to eval/reports, a library of their
own, not the app's. The table goes to eval/results/<date>[_label].md (plus .json), next to the
previous run's averages. Two metrics are judged by FAST_LLM: off-topic sources, and citation
accuracy in the spirit of DeepResearch Bench's FACT (sampled «statement → link» pairs, the page
fetched again, one verdict per pair). Judging is billed separately from the reports.
"""

import os
import sys
from pathlib import Path

EVAL_DIR = Path(__file__).resolve().parent
os.environ.setdefault("REPORTS_DIR", str(EVAL_DIR / "reports"))  # read when my_browser is imported
sys.path[:0] = [str(EVAL_DIR.parent), str(EVAL_DIR)]  # my_browser and metrics, however the script is started

import argparse  # noqa: E402
import asyncio  # noqa: E402
import json  # noqa: E402
import logging  # noqa: E402
import random  # noqa: E402
import shutil  # noqa: E402
import subprocess  # noqa: E402
import time  # noqa: E402
import zlib  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import metrics  # noqa: E402
from my_browser import costs, suggest  # noqa: E402
from my_browser.pipeline import (MODES, REPORTS_DIR, ROOT, SOURCES, WRITERS, PreflightError,  # noqa: E402
                                 env_overrides, overrides, preflight, research, save)
from my_browser.sources import SEARCH_META  # noqa: E402

RESULTS_DIR = EVAL_DIR / "results"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
VERDICTS = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "NO_TEXT")

OFF_TOPIC_PROMPT = """Research topic: "{query}".
For each source below answer 1 if it is off-topic (about something else, even if some words match) and 0 if it is on-topic.

{listing}

Return ONLY a JSON array of {count} integers (0 or 1) in the same order."""

CITATION_PROMPT = """You check one citation from a research report. The statement may be in Russian while the source is in English.

Statement: {statement}

Excerpts from the cited page ({url}):
{excerpts}

Does the page support the statement? Answer with one word:
SUPPORTED - the excerpts say this (paraphrase and rounding are fine)
PARTIAL - they support only part of it, or the numbers differ
UNSUPPORTED - they do not say this, or contradict it
NO_TEXT - the excerpts are empty, a paywall, a cookie banner or an error page"""

# (key, column header, format)
COLUMNS = [
    ("words", "Слов", "int"), ("seconds", "Время", "mmss"), ("rub", "₽", "rub"),
    ("sources", "Источн.", "int"), ("domains", "Доменов", "int"), ("english", "Англ.", "pct"),
    ("authoritative", "Автор.", "pct"), ("score3", "3/3", "pct"), ("in_report", "В отчёте", "pct"),
    ("linked_paragraphs", "Абзацы со ссылкой", "pct"), ("failed", "Не скачалось", "pct"),
    ("off_topic", "Не по теме", "pct"), ("citation_accuracy", "Точность цитат", "pct"), ("checked", "Проверено", "int"),
]

METHOD = """Как считается:
- Англ. — источники без кириллицы в адресе, заголовке и фрагменте и не на российских доменах.
- Автор. — домены из верхнего списка `my_browser/sources.py` и gov/edu: наука, международные организации, агентства.
- 3/3 — доля источников, которым отбор поставил высшую оценку.
- В отчёте — источники, ссылка на которые есть в тексте или в списке источников.
- Абзацы со ссылкой — абзацы и пункты списков от 25 слов хотя бы с одной ссылкой.
- Не скачалось — страницы, которые скрейпер не смог прочитать.
- В подробном режиме список источников отчёта — это процитированные ссылки, поэтому «В отчёте» там всегда 100%, а «Источн.», «Англ.» и «Автор.» считаются только по ним.
- Не по теме — оценка Flash по адресу и фрагменту из поисковика (заголовков SearXNG не отдаёт). Ссылкам arXiv и doi.org без описания судья часто ставит «не по теме», поэтому цифра завышена и годится только для сравнения замеров между собой.
- Точность цитат — до {pairs} случайных пар «утверждение → ссылка» на отчёт. Страница скачивается заново, \
Flash сравнивает утверждение с 4 самыми похожими фрагментами: подтверждено = 1, частично = 0,5. \
Пары, где текст страницы недоступен, не считаются; «Проверено» — сколько пар осталось."""


class LogLines(logging.Handler):
    """Collects gpt-researcher's log lines: accepted sources and scrape failures."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


LOG = LogLines()
for _name in ("scraper", "gpt_researcher.scraper.scraper"):
    logging.getLogger(_name).addHandler(LOG)


def load_topics() -> list[str]:
    lines = (EVAL_DIR / "topics.txt").read_text("utf-8").splitlines()
    return [line.strip() for line in lines if line.strip() and not line.lstrip().startswith("#")]


def describe(url: str) -> str:
    meta = SEARCH_META.get(url, {})
    return meta.get("title") or meta.get("snippet", "")[:160]


async def off_topic_share(query: str, urls: list[str]) -> float | None:
    if not urls:
        return None
    listing = "\n".join(f"{i}. {url} — {describe(url)}" for i, url in enumerate(urls, 1))
    answer = await suggest.fast(OFF_TOPIC_PROMPT.format(query=query, listing=listing, count=len(urls)), max_tokens=400, temperature=0)
    flags = suggest.json_list(answer)
    return metrics.share(sum(str(f).strip() == "1" for f in flags), len(urls)) if len(flags) == len(urls) else None


async def fetch_pages(urls: set[str]) -> dict[str, str]:
    from gpt_researcher.scraper.scraper import Scraper
    from gpt_researcher.utils.workers import WorkerPool

    if not urls:
        return {}
    results = await Scraper(sorted(urls), USER_AGENT, "bs", WorkerPool(8)).run()
    return {r["url"]: r["raw_content"] for r in results if r.get("raw_content")}


async def citation_accuracy(query: str, markdown: str, pairs: int) -> dict:
    found = metrics.citation_pairs(markdown)
    sample = random.Random(zlib.crc32(query.encode())).sample(found, min(pairs, len(found)))
    pages = await fetch_pages({url for _, url in sample})
    limit = asyncio.Semaphore(5)

    async def judge(statement: str, url: str) -> str:
        if not pages.get(url):
            return "NO_TEXT"
        excerpts = "\n\n".join(f"[{i}] {chunk}" for i, chunk in enumerate(metrics.excerpts(statement, pages[url]), 1))
        async with limit:
            answer = await suggest.fast(CITATION_PROMPT.format(statement=statement, url=url, excerpts=excerpts),
                                        max_tokens=10, temperature=0)
        word = (answer.split() or ["?"])[0].strip(".:,*").upper()
        return word if word in VERDICTS else "NO_TEXT"

    verdicts = Counter(await asyncio.gather(*(judge(s, u) for s, u in sample)))
    checked = verdicts["SUPPORTED"] + verdicts["PARTIAL"] + verdicts["UNSUPPORTED"]
    return {
        "linked_statements": len(found),
        "checked": checked,
        "citation_accuracy": round((verdicts["SUPPORTED"] + 0.5 * verdicts["PARTIAL"]) / checked, 3) if checked else None,
        "verdicts": dict(verdicts),
    }


async def measure(query: str, args: argparse.Namespace, judging: costs.Meter) -> dict:
    LOG.lines.clear()
    meter = costs.Meter()
    token = costs.current.set(meter)
    started = time.monotonic()
    try:
        markdown, urls = await research(query, args.mode, on_progress=lambda text: None)
    finally:
        costs.current.reset(token)
    seconds = time.monotonic() - started
    report = save(query, args.mode, markdown, urls, seconds, pdf=False, cost=meter.summary())
    row = {
        "topic": query, "report": report.md_path.name, "words": report.words, "seconds": round(seconds),
        "rub": round(meter.rub(), 2), "truncated": meter.truncated_calls,
        **metrics.sources(urls, {url: describe(url) for url in urls}),
        **metrics.citations(urls, markdown),
        **metrics.scrape_log(LOG.lines),
    }
    token = costs.current.set(judging)
    try:
        row["off_topic"] = await off_topic_share(query, urls)
        row.update(await citation_accuracy(query, markdown, args.pairs))
    finally:
        costs.current.reset(token)
    return row


async def run_all(topics: list[tuple[int, str]], args: argparse.Namespace) -> tuple[list[dict], costs.Meter]:
    judging, rows = costs.Meter(), []
    for n, query in topics:
        print(f"\n=== [{n}] {query}", flush=True)
        try:
            row = await measure(query, args, judging)
        except Exception as e:
            logging.exception("Topic failed: %s", query)
            row = {"topic": query, "error": str(e)[:300]}
        rows.append({"n": n, **row})
        print(json.dumps(rows[-1], ensure_ascii=False), flush=True)
    return rows, judging


def cell(value, kind: str) -> str:
    if value is None:
        return "—"
    if kind == "pct":
        return f"{value * 100:.0f}%"
    if kind == "mmss":
        return f"{round(value) // 60}:{round(value) % 60:02d}"
    if kind == "rub":
        return f"{value:.2f}".replace(".", ",")
    return f"{value:.0f}" if isinstance(value, float) else str(value)


def averages(rows: list[dict]) -> dict:
    result = {}
    for key, _, _ in COLUMNS:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        result[key] = round(sum(values) / len(values), 3) if values else None
    return result


def git_version() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short", "HEAD") + (" + незакоммиченные правки" if git("status", "--porcelain") else "")


def write_results(rows: list[dict], judging: costs.Meter, args: argparse.Namespace, started: datetime) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    previous = sorted(RESULTS_DIR.glob("*.json"))
    name = f"{started:%Y-%m-%d_%H%M}" + (f"_{args.label}" if args.label else "")
    avg = averages(rows)
    header = "| # | Тема | " + " | ".join(h for _, h, _ in COLUMNS) + " |"
    lines = [
        f"# Замер {started:%d.%m.%Y %H:%M}" + (f" · {args.label}" if args.label else ""),
        "",
        f"- Коммит: {git_version()}",
        f"- Режим: {args.mode} · текст пишет {args.writer} · источники: {args.sources} ({SOURCES[args.sources]})",
        f"- Тарифы DeepSeek: {'пик' if costs.is_peak(started.astimezone(timezone.utc)) else 'вне пика'}",
        f"- Стоимость: отчёты {cell(sum(r.get('rub') or 0 for r in rows), 'rub')} ₽, оценка {cell(judging.rub(), 'rub')} ₽",
        "",
        header,
        "|" + "---|" * (len(COLUMNS) + 2),
    ]
    for r in rows:
        topic = r["topic"] if len(r["topic"]) <= 42 else r["topic"][:41] + "…"
        cells = [f"ошибка: {r['error'][:80]}"] if r.get("error") else [cell(r.get(k), kind) for k, _, kind in COLUMNS]
        lines.append(f"| {r['n']} | {topic} | " + " | ".join(cells) + " |")
    lines.append("| | **Среднее** | " + " | ".join(cell(avg[k], kind) for k, _, kind in COLUMNS) + " |")
    if previous:
        before = json.loads(previous[-1].read_text("utf-8"))
        lines.append(f"| | Прошлый замер ({previous[-1].stem}) | "
                     + " | ".join(cell(before["averages"].get(k), kind) for k, _, kind in COLUMNS) + " |")
    lines += ["", METHOD.format(pairs=args.pairs), ""]
    (RESULTS_DIR / f"{name}.md").write_text("\n".join(lines), "utf-8")
    run = {"started": started.isoformat(timespec="seconds"), "commit": git_version(), "mode": args.mode,
           "writer": args.writer, "sources": SOURCES[args.sources], "pairs": args.pairs, "judging_rub": round(judging.rub(), 2)}
    (RESULTS_DIR / f"{name}.json").write_text(
        json.dumps({"run": run, "averages": avg, "rows": rows}, ensure_ascii=False, indent=2), "utf-8")
    return RESULTS_DIR / f"{name}.md"


def main() -> None:
    parser = argparse.ArgumentParser(description="Замер качества поиска на постоянном наборе тем")
    parser.add_argument("--topics", help="номера тем через запятую, по умолчанию все")
    parser.add_argument("--mode", choices=MODES, default="quick")
    parser.add_argument("--writer", choices=WRITERS, default="flash")
    parser.add_argument("--sources", choices=SOURCES, default="all")
    parser.add_argument("--pairs", type=int, default=20, help="сколько пар «утверждение → ссылка» проверять в отчёте")
    parser.add_argument("--label", default="", help="приписка к имени файла с результатами")
    args = parser.parse_args()

    topics = list(enumerate(load_topics(), 1))
    if args.topics:
        wanted = {int(n) for n in args.topics.split(",")}
        topics = [(n, t) for n, t in topics if n in wanted]
    blocklist = ROOT / "reports" / "blocked_domains.txt"
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    if blocklist.exists() and REPORTS_DIR != blocklist.parent:
        shutil.copy(blocklist, REPORTS_DIR / blocklist.name)  # the same blocklist as the app

    started = datetime.now().astimezone()
    with env_overrides(overrides(args.sources, writer=args.writer)):
        try:
            preflight()
        except PreflightError as e:
            sys.exit(str(e))
        rows, judging = asyncio.run(run_all(topics, args))
    path = write_results(rows, judging, args, started)
    print("\n" + path.read_text("utf-8"))


if __name__ == "__main__":
    main()

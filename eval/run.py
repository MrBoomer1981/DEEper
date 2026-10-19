"""Measure research quality on a fixed set of topics (eval/topics.txt).

    uv run eval/run.py                            # every topic: quick report, Flash writes the text
    uv run eval/run.py --topics 1,4 --label exa   # a subset, with a name for the results file
    uv run eval/run.py --topics 1 --mode detailed --writer pro
    uv run eval/run.py --factcheck                # with the fact-check: each report judged before and after its fixes
    uv run eval/run.py --rejudge 2026-10-18_2327_3.1-reader   # that run's reports judged again by the current judge

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
import subprocess  # noqa: E402
import time  # noqa: E402
import zlib  # noqa: E402
from collections import Counter  # noqa: E402
from datetime import datetime, timezone  # noqa: E402

import metrics  # noqa: E402
from my_browser.common import costs  # noqa: E402
from my_browser.common.settings import MODES, ROOT, SOURCES, WRITERS  # noqa: E402
from my_browser.core.library import Library  # noqa: E402
from my_browser.worker import factcheck, hooks, suggest  # noqa: E402
from my_browser.worker.hooks import SEARCH_META  # noqa: E402
from my_browser.worker.pipeline import PreflightError, config_file, preflight, render, research, run_settings  # noqa: E402

RESULTS_DIR = EVAL_DIR / "results"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
VERDICTS = ("SUPPORTED", "PARTIAL", "UNSUPPORTED", "NO_TEXT")
# 2 (19.10.2026): excerpts found also by the statement's English translation; the sentence before it given.
# 3 (19.10.2026): pairs keep URLs with parentheses whole and skip bare lists of sources (factcheck.claims()).
JUDGE = 3

OFF_TOPIC_PROMPT = """Research topic: "{query}".
For each source below answer 1 if it is off-topic (about something else, even if some words match) and 0 if it is on-topic.

{listing}

Return ONLY a JSON array of {count} integers (0 or 1) in the same order."""

CITATION_PROMPT = """You check one citation from a research report. The statement may be in Russian while the source is in English.

The sentence before it in the report, only so that words like «их» make sense (not checked): {context}

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
    ("linked_paragraphs", "Абзацы со ссылкой", "pct"), ("failed", "Не скачалось", "pct"), ("rescued", "Спасено", "pct"),
    ("off_topic", "Не по теме", "pct"), ("citation_accuracy", "Точность цитат", "pct"), ("checked", "Проверено", "int"),
]
FACTCHECK_COLUMNS = [
    ("citation_accuracy_before", "Точность до проверки", "pct"), ("fc_fixed", "Исправлено", "int"),
    ("fc_doubtful", "Сомнит.", "int"),
]

METHOD = """Как считается:
- Англ. — источники без кириллицы в адресе, заголовке и фрагменте и не на российских доменах.
- Автор. — домены из верхнего списка `my_browser/search/sources.py` и gov/edu: наука, международные организации, агентства.
- 3/3 — доля источников, которым отбор поставил высшую оценку.
- В отчёте — источники, ссылка на которые есть в тексте или в списке источников.
- Абзацы со ссылкой — абзацы и пункты списков от 25 слов хотя бы с одной ссылкой.
- Не скачалось — страницы, которые не удалось прочитать ни напрямую, ни в обход.
- Спасено — страницы, прочитанные только в обход (`my_browser/search/reader.py`): Europe PMC, открытая копия через OpenAlex, Jina Reader или аннотация OpenAlex.
- В подробном режиме список источников отчёта — это процитированные ссылки, поэтому «В отчёте» там всегда 100%, а «Источн.», «Англ.» и «Автор.» считаются только по ним.
- Не по теме — оценка Flash по адресу и фрагменту из поисковика (заголовков SearXNG не отдаёт). Ссылкам arXiv и doi.org без описания судья часто ставит «не по теме», поэтому цифра завышена и годится только для сравнения замеров между собой.
- Точность цитат — до {pairs} случайных пар «утверждение → ссылка» на отчёт. Страница скачивается заново, \
Flash сравнивает утверждение с 4 фрагментами страницы, самыми похожими на него и на его английский перевод \
(BM25 по словам и совпадающие числа; до 19.10.2026 фрагменты искались без перевода, и русские утверждения \
на английских страницах часто получали не те фрагменты). Подтверждено = 1, частично = 0,5. \
Пары, где текст страницы недоступен, не считаются; «Проверено» — сколько пар осталось."""

FACTCHECK_METHOD = """- С проверкой фактов (`--factcheck`): «Точность до проверки» — та же оценка текста до исправлений, \
«Исправлено» — утверждения, у которых проверка заменила ссылку или переписала предложение, \
«Сомнит.» — утверждения, которые остались неподтверждёнными. Судья замера независим от проверки \
только отчасти: он похож на неё по устройству, хотя страницы скачивает заново и выбирает пары случайно."""


class LogLines(logging.Handler):
    """Collects gpt-researcher's log lines: accepted sources and scrape failures."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


LOG = LogLines()
for _name in ("scraper", "gpt_researcher.scraper.scraper", "my_browser.search.reader"):
    logging.getLogger(_name).addHandler(LOG)
logging.getLogger("my_browser.search.reader").setLevel(logging.INFO)  # its outcome lines are INFO


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


async def citation_accuracy(query: str, markdown: str, args: argparse.Namespace) -> dict:
    from gpt_researcher.config import Config

    found = factcheck.claims(markdown)
    sample = random.Random(zlib.crc32(query.encode())).sample(found, min(args.pairs, len(found)))
    passages = factcheck.Passages(await fetch_pages({claim.url for claim in sample}))
    translations = await factcheck.translate([claim.statement for claim in sample], Config(str(args.config_path)))
    limit = asyncio.Semaphore(5)

    async def judge(claim: factcheck.Claim, translation: str) -> str:
        page = passages.find(claim.url)
        if not page:
            return "NO_TEXT"
        chunks = passages.excerpts(page, passages.query(claim.statement, translation))
        excerpts = "\n\n".join(f"[{i}] {chunk}" for i, chunk in enumerate(chunks, 1))
        async with limit:
            answer = await suggest.fast(CITATION_PROMPT.format(statement=claim.statement, context=claim.context or "—",
                                                               url=claim.url, excerpts=excerpts), max_tokens=10, temperature=0)
        word = (answer.split() or ["?"])[0].strip(".:,*").upper()
        return word if word in VERDICTS else "NO_TEXT"

    verdicts = Counter(await asyncio.gather(*(judge(c, t) for c, t in zip(sample, translations))))
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
    started, check = time.monotonic(), None
    try:
        result = await research(query, args.mode, args.config_path, on_progress=lambda text: None)
        markdown, urls = result.markdown, result.sources
        if args.factcheck:
            markdown, check = await factcheck.run(markdown, result.pages, args.config_path)
    finally:
        costs.current.reset(token)
    seconds = time.monotonic() - started
    library = Library()
    report = library.get(library.store(render(query, args.mode, markdown, urls, seconds, args.settings,
                                              cost=meter.summary(), factcheck=check, pdf=False)))
    row = {
        "topic": query, "report": report.md_path.name, "words": report.words, "seconds": round(seconds),
        "rub": round(meter.rub(), 2), "truncated": meter.truncated_calls,
        **metrics.sources(urls, {url: describe(url) for url in urls}),
        **metrics.citations(urls, markdown),
        **metrics.scrape_log(LOG.lines),
    }
    if check:
        row.update(fc_fixed=check.get("relinked", 0) + check.get("rewritten", 0), fc_doubtful=len(check.get("doubtful", [])),
                   fc_claims=check.get("claims"), fc_accuracy=check.get("accuracy"), fc_rub=check.get("cost_rub"),
                   fc_seconds=check.get("seconds"), fc_error=check.get("error"))
    token = costs.current.set(judging)
    try:
        row["off_topic"] = await off_topic_share(query, urls)
        row.update(await citation_accuracy(query, markdown, args))
        if check:
            row["citation_accuracy_before"] = (await citation_accuracy(query, result.markdown, args))["citation_accuracy"]
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


def columns(args: argparse.Namespace) -> list[tuple[str, str, str]]:
    return COLUMNS + (FACTCHECK_COLUMNS if args.factcheck else [])


def averages(rows: list[dict], cols: list[tuple[str, str, str]]) -> dict:
    result = {}
    for key, _, _ in cols:
        values = [r[key] for r in rows if isinstance(r.get(key), (int, float))]
        result[key] = round(sum(values) / len(values), 3) if values else None
    return result


def git_version() -> str:
    def git(*args: str) -> str:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True).stdout.strip()
    return git("rev-parse", "--short", "HEAD") + (" + незакоммиченные правки" if git("status", "--porcelain") else "")


def write_results(rows: list[dict], judging: costs.Meter, args: argparse.Namespace, started: datetime) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    cols = columns(args)
    same_setup = {"mode": args.mode, "writer": args.writer, "sources": SOURCES[args.sources], "factcheck": args.factcheck, "judge": JUDGE}
    defaults = {"factcheck": False, "judge": 1}  # runs made before these settings existed
    previous = [p for p in sorted(RESULTS_DIR.glob("*.json"))  # the last run made the same way
                if all(json.loads(p.read_text("utf-8"))["run"].get(k, defaults.get(k)) == v for k, v in same_setup.items())]
    name = f"{started:%Y-%m-%d_%H%M}" + (f"_{args.label}" if args.label else "")
    avg = averages(rows, cols)
    checking = sum(r.get("fc_rub") or 0 for r in rows)
    header = "| # | Тема | " + " | ".join(h for _, h, _ in cols) + " |"
    lines = [
        f"# Замер {started:%d.%m.%Y %H:%M}" + (f" · {args.label}" if args.label else ""),
        "",
        f"- Коммит: {git_version()}",
        f"- Режим: {args.mode} · текст пишет {args.writer} · источники: {args.sources} ({SOURCES[args.sources]})"
        + (" · с проверкой фактов" if args.factcheck else ""),
        f"- Тарифы DeepSeek: {'пик' if costs.is_peak(started.astimezone(timezone.utc)) else 'вне пика'}",
        f"- Стоимость: отчёты {cell(sum(r.get('rub') or 0 for r in rows), 'rub')} ₽"
        + (f" (из них проверка фактов {cell(checking, 'rub')} ₽)" if args.factcheck else "")
        + f", оценка {cell(judging.rub(), 'rub')} ₽",
    ]
    if getattr(args, "rejudged", None):
        lines.append(f"- Перепроверка отчётов замера {args.rejudged}: отчёты и метрики поиска оттуда, точность цитат — заново")
    lines += ["", header, "|" + "---|" * (len(cols) + 2)]
    for r in rows:
        topic = r["topic"] if len(r["topic"]) <= 42 else r["topic"][:41] + "…"
        cells = [f"ошибка: {r['error'][:80]}"] if r.get("error") else [cell(r.get(k), kind) for k, _, kind in cols]
        lines.append(f"| {r['n']} | {topic} | " + " | ".join(cells) + " |")
    lines.append("| | **Среднее** | " + " | ".join(cell(avg[k], kind) for k, _, kind in cols) + " |")
    if previous:
        before = json.loads(previous[-1].read_text("utf-8"))
        lines.append(f"| | Прошлый замер ({previous[-1].stem}) | "
                     + " | ".join(cell(before["averages"].get(k), kind) for k, _, kind in cols) + " |")
    lines += ["", METHOD.format(pairs=args.pairs)] + ([FACTCHECK_METHOD] if args.factcheck else []) + [""]
    (RESULTS_DIR / f"{name}.md").write_text("\n".join(lines), "utf-8")
    run = {"started": started.isoformat(timespec="seconds"), "commit": git_version(), "mode": args.mode,
           "writer": args.writer, "sources": SOURCES[args.sources], "factcheck": args.factcheck, "judge": JUDGE,
           "pairs": args.pairs, "judging_rub": round(judging.rub(), 2)}
    if getattr(args, "rejudged", None):
        run["rejudged"] = args.rejudged
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
    parser.add_argument("--factcheck", action="store_true", help="проверять факты и оценивать отчёт до и после исправлений")
    parser.add_argument("--rejudge", metavar="ЗАМЕР", help="заново оценить точность цитат в отчётах прошлого замера "
                                                          "(имя файла из eval/results без расширения), ничего не исследуя")
    args = parser.parse_args()
    if args.rejudge:
        rejudge(args)
        return

    topics = list(enumerate(load_topics(), 1))
    if args.topics:
        wanted = {int(n) for n in args.topics.split(",")}
        topics = [(n, t) for n, t in topics if n in wanted]
    blocklist = ROOT / "reports" / "blocked_domains.txt"  # the same blocklist as the app
    hooks.blocked.set([line.strip().lower() for line in blocklist.read_text("utf-8").splitlines() if line.strip()]
                      if blocklist.exists() else [])
    args.settings = run_settings(args.sources, writer=args.writer)
    try:
        preflight(args.settings)
    except PreflightError as e:
        sys.exit(str(e))

    started = datetime.now().astimezone()
    with config_file(args.settings) as path:
        args.config_path = path
        rows, judging = asyncio.run(run_all(topics, args))
    path = write_results(rows, judging, args, started)
    print("\n" + path.read_text("utf-8"))


def rejudge(args: argparse.Namespace) -> None:
    """Judge the citations of a past run's reports again, e.g. after the judge changed; nothing is researched."""
    source = json.loads((RESULTS_DIR / f"{args.rejudge}.json").read_text("utf-8"))
    run = source["run"]
    args.mode, args.writer, args.pairs = run["mode"], run["writer"], run.get("pairs", args.pairs)
    args.sources = next((key for key, value in SOURCES.items() if value == run["sources"]), args.sources)
    args.factcheck, args.rejudged = run.get("factcheck", False), args.rejudge
    args.label = args.label or f"{args.rejudge.split('_', 2)[-1]}-rejudged"
    args.settings = run_settings(args.sources, writer=args.writer)
    try:
        preflight(args.settings)
    except PreflightError as e:
        sys.exit(str(e))

    async def judge_all() -> tuple[list[dict], costs.Meter]:
        judging, rows = costs.Meter(), []
        token = costs.current.set(judging)
        try:
            for row in source["rows"]:
                if not row.get("error"):
                    markdown = (EVAL_DIR / "reports" / row["report"]).read_text("utf-8")
                    row = {**row, **await citation_accuracy(row["topic"], markdown, args)}
                    print(f"[{row['n']}] {row['citation_accuracy']} {row['verdicts']}", flush=True)
                rows.append(row)
        finally:
            costs.current.reset(token)
        return rows, judging

    started = datetime.now().astimezone()
    with config_file(args.settings) as path:
        args.config_path = path
        rows, judging = asyncio.run(judge_all())
    print("\n" + write_results(rows, judging, args, started).read_text("utf-8"))


if __name__ == "__main__":
    main()

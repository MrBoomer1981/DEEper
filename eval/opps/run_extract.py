"""Extraction eval for the opportunities lane: how well the model reads announcements (my_browser/news/opps.py).

    uv run eval/opps/run_extract.py                 # every case, real Flash (about 0.5 ₽)
    uv run eval/opps/run_extract.py --label prompt2 # names the results file

`cases.jsonl` holds real posts of the collection of 25.10.2026, labeled by hand with the accepted answers: whether it
is an opportunity at all and, for one, the deadline's day (null when the text gives none), the types and the
ru_access values that are right, and whether the text says applications are closed. Whether the model calls one
especially big (`major`) is not labeled: the results list those it marked, to be read by eye. The cases go through
opps.describe() and opps.read() in batches of opps.EXTRACT_BATCH, with today fixed at 25.10.2026, and the model's
deadline through opps.deadline(): the prompt and the parsing measured are those the loop runs. A missed opportunity
counts as a miss of every field. Results go to eval/opps/results/<date>_<time>_<label>.md and .json.
"""

import argparse
import asyncio
import json
import os
import sys
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("REPORTS_DIR", tempfile.mkdtemp(prefix="eval-opps-"))  # its spend goes to a throwaway database
sys.path.insert(0, str(ROOT))

from my_browser.news import config, db, opps  # noqa: E402

HERE = Path(__file__).parent
TODAY = "25.10.2026"
NOW = datetime(2026, 10, 25, 12, 0, tzinfo=opps.MOSCOW).timestamp()


def day(answer: dict, lang: str) -> str | None:
    """The deadline's day as the loop would store it, or None."""
    if answer.get("deadline_kind") == "rolling":
        return None
    iso, _, _ = opps.deadline(answer.get("deadline"), answer.get("deadline_tz"), lang, NOW)
    return iso[:10] if iso else None


def judge(case: dict, answer: dict | None) -> dict:
    """Which fields the answer got right."""
    label, lang = case["label"], case["posts"][0]["lang"]
    said = bool(answer and answer.get("is_opportunity"))
    result = {"opportunity": said == label["is_opportunity"]}
    if label["is_opportunity"]:
        answer = answer if said else {}
        result |= {"deadline": said and day(answer, lang) in label["deadline"],
                   "ru_access": said and answer.get("ru_access") in label["ru_access"],
                   "type": said and answer.get("opp_type") in label["opp_type"],
                   "closed": said and bool(answer.get("closed")) == label["closed"]}
    return result


async def run(cases: list[dict]) -> tuple[list[dict | None], float, int]:
    settings = config.load()
    answers: list[dict | None] = []
    for start in range(0, len(cases), opps.EXTRACT_BATCH):
        batch = cases[start:start + opps.EXTRACT_BATCH]
        blocks = "\n\n".join(opps.describe([{**p, "links": json.dumps(p["links"])} for p in case["posts"]], n + 1)
                             for n, case in enumerate(batch))
        got = await opps.read(settings, blocks, TODAY, config.clean_profile(settings.opps_profile))
        answers += [got.get(n + 1) for n in range(len(batch))]
    with db.connect() as conn:
        rub, calls = conn.execute("SELECT COALESCE(SUM(rub), 0), COALESCE(SUM(calls), 0) FROM opps_spend").fetchone()
    return answers, rub, calls


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--label", default="extract")
    args = parser.parse_args()
    cases = [json.loads(line) for line in (HERE / "cases.jsonl").read_text().splitlines() if line.strip()]
    answers, rub, calls = asyncio.run(run(cases))
    results = [judge(case, answer) for case, answer in zip(cases, answers)]
    opportunities = [r for case, r in zip(cases, results) if case["label"]["is_opportunity"]]

    def share(rows: list[dict], key: str) -> str:
        return f"{sum(r[key] for r in rows) / len(rows):.2f} ({sum(r[key] for r in rows)}/{len(rows)})" if rows else "-"

    lines = [f"# Opportunities extraction eval, {datetime.now():%d.%m.%Y %H:%M} ({args.label})", "",
             f"{len(cases)} cases, {len(opportunities)} of them opportunities; today fixed at {TODAY}. "
             f"Model: `{os.environ.get('FAST_LLM') or 'config.json FAST_LLM'}`. Cost {rub:.2f} ₽ in {calls} calls.", "",
             "| metric | share |", "|---|---|",
             f"| is an opportunity (all cases) | {share(results, 'opportunity')} |",
             f"| deadline: the same day, or none when none (opportunities) | {share(opportunities, 'deadline')} |",
             f"| ru_access | {share(opportunities, 'ru_access')} |",
             f"| type | {share(opportunities, 'type')} |",
             f"| closed | {share(opportunities, 'closed')} |", "", "## Misses", ""]
    raw = []
    for case, answer, result in zip(cases, answers, results):
        answer = answer or {}
        missed = [key for key, ok in result.items() if not ok]
        raw.append({"id": case["id"], "label": case["label"], "answer": answer, "missed": missed})
        if not missed:
            continue
        label = case["label"]
        got = {"opportunity": answer.get("is_opportunity"), "deadline": f"{day(answer, case['posts'][0]['lang'])} "
               f"({answer.get('deadline_text')!r}, {answer.get('deadline_kind')})", "ru_access": answer.get("ru_access"),
               "type": answer.get("opp_type"), "closed": answer.get("closed")}
        want = {"opportunity": label["is_opportunity"], "deadline": label.get("deadline"), "ru_access": label.get("ru_access"),
                "type": label.get("opp_type"), "closed": label.get("closed")}
        details = "; ".join(f"{key}: {got[key]} instead of {want[key]}" for key in missed)
        lines.append(f"- {case['id']} «{case['posts'][0]['title'][:70]}» ({case['note']}): {details}"
                     + (f". Reason: {answer.get('reason')}" if "opportunity" in missed and answer.get("reason") else ""))
    if lines[-1] == "":
        lines.append("None.")
    # `major` (shown within the app's longer window) has no labels: listed to be read by eye
    major = [(case, answer) for case, answer in zip(cases, answers) if answer and answer.get("major") is True]
    lines += ["", f"## Marked especially big (`major`): {len(major)} of {len(opportunities)}", ""]
    lines += [f"- {case['id']} «{(answer.get('title_ru') or case['posts'][0]['title'])[:80]}» ({answer.get('opp_type')}, "
              f"{answer.get('organizer') or '?'})" for case, answer in major] or ["None."]
    out = HERE / "results" / f"{datetime.now():%Y-%m-%d_%H%M}_{args.label}"
    out.parent.mkdir(exist_ok=True)
    out.with_suffix(".md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    out.with_suffix(".json").write_text(json.dumps(raw, ensure_ascii=False, indent=1), encoding="utf-8")
    print("\n".join(lines[:13]))
    print(f"\n{sum(1 for r in raw if r['missed'])} cases with misses; written to {out}.md")


if __name__ == "__main__":
    main()

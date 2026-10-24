"""Dedup eval for the news part: how well «same story» is told apart, per model and threshold.

    uv run eval/news/run_dedup.py                       # every model in MODELS, embeddings alone and with links
    uv run eval/news/run_dedup.py --models minilm       # one model
    uv run eval/news/run_dedup.py --label after-titles  # names the results file

`pairs.jsonl` holds pairs of posts from a live collection (24.10.2026), picked across similarity ranges and labeled
by hand: `same` is true when both report one event, false otherwise, null for digest posts of several stories
(the news part keeps those out of merging). Precision and recall are over the labeled pairs, so they compare
models and thresholds; they are not the share of duplicates in a real digest.
Results go to eval/news/results/<date>_<label>.md.
"""

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
os.environ.setdefault("REPORTS_DIR", str(ROOT / "eval" / "reports"))  # the models are cached there, not in the app's

from my_browser.news import embed  # noqa: E402
from my_browser.news.collectors import usable_key  # noqa: E402

HERE = Path(__file__).parent
MODELS = {
    "minilm": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",  # 0.22 GB
    "mpnet": "sentence-transformers/paraphrase-multilingual-mpnet-base-v2",  # 1 GB
}
THRESHOLDS = [round(0.50 + 0.02 * i, 2) for i in range(23)]  # 0.50 … 0.94
MIN_PRECISION = 0.95  # a wrong merge hides a story; a missed one only repeats it


def scores(predicted: list[bool], truth: list[bool]) -> tuple[float, float, float]:
    tp = sum(p and t for p, t in zip(predicted, truth))
    fp = sum(p and not t for p, t in zip(predicted, truth))
    fn = sum(t and not p for p, t in zip(predicted, truth))
    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return precision, recall, f1


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--models", default=",".join(MODELS))
    parser.add_argument("--label", default="dedup")
    args = parser.parse_args()

    pairs = [p for p in map(json.loads, (HERE / "pairs.jsonl").read_text().splitlines()) if p["same"] is not None]
    truth = [p["same"] for p in pairs]
    linked = [any(usable_key(k) for k in p["shared_links"]) for p in pairs]
    lines = [f"# Dedup eval, {datetime.now():%d.%m.%Y %H:%M}", "",
             f"{len(pairs)} labeled pairs: {sum(truth)} same, {len(truth) - sum(truth)} different. "
             f"A shared link alone: precision {scores(linked, truth)[0]:.2f}, recall {scores(linked, truth)[1]:.2f}.", ""]
    for key in args.models.split(","):
        texts = [embed.text(side["title"], side["text"]) for p in pairs for side in (p["a"], p["b"])]
        vectors = embed.vectors(texts, MODELS[key])
        sims = [float(vectors[2 * i] @ vectors[2 * i + 1]) for i in range(len(pairs))]
        lines += [f"## {key} ({MODELS[key]})", "", "| threshold | P | R | F1 | P with links | R with links | F1 with links |",
                  "|---|---|---|---|---|---|---|"]
        best = None
        for t in THRESHOLDS:
            alone = scores([s >= t for s in sims], truth)
            both = scores([s >= t or link for s, link in zip(sims, linked)], truth)
            lines.append(f"| {t:.2f} | {alone[0]:.2f} | {alone[1]:.2f} | {alone[2]:.2f} | {both[0]:.2f} | {both[1]:.2f} | {both[2]:.2f} |")
            if both[0] >= MIN_PRECISION and (best is None or both[2] > best[1][2]):
                best = (t, both)
        misses = sorted((s, p["a"]["title"][:60], p["b"]["title"][:60]) for s, p, link in zip(sims, pairs, linked)
                        if p["same"] and not link and best and s < best[0])
        wrong = sorted(((s, p["a"]["title"][:60], p["b"]["title"][:60]) for s, p in zip(sims, pairs)
                        if not p["same"] and best and s >= best[0]), reverse=True)
        lines += ["", f"Best with links at precision ≥ {MIN_PRECISION}: " + (
            f"threshold {best[0]:.2f}, P {best[1][0]:.2f}, R {best[1][1]:.2f}, F1 {best[1][2]:.2f}." if best else "none."), ""]
        lines += [f"- missed {s:.2f}: {a} | {b}" for s, a, b in misses[:8]]
        lines += [f"- wrongly merged {s:.2f}: {a} | {b}" for s, a, b in wrong[:8]] + [""]
    report = "\n".join(lines)
    print(report)
    out = HERE / "results" / f"{datetime.now():%Y-%m-%d_%H%M}_{args.label}.md"
    out.parent.mkdir(exist_ok=True)
    out.write_text(report + "\n")
    print(f"\n-> {out.relative_to(ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()

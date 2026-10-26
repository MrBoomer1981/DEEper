# CLAUDE.md: eval

`eval/` measures research quality on a fixed set of topics (`topics.txt`). It is the project's measurement harness, not a test suite: the user decided against tests while the project is in active development and kept eval. This file covers what it measures, how to run it, and what it relies on in the parts. The repository's rules for working in one part are in the root CLAUDE.md. Paths below are relative to the repository root.

## What eval does

- `eval/run.py` runs each topic through `pipeline.research()` (search in-process) with `REPORTS_DIR=eval/reports`, a library of its own, and the app's blocklist (`reports/blocked_domains.txt`).
- `eval/metrics.py` computes source and citation stats from the saved report and from the captured log lines of the `scraper`, `gpt_researcher.scraper.scraper` and `my_browser.search.reader` loggers (accepted sources with scores, fetch failures, rescues).
- Two FAST_LLM judges, billed apart from the reports:
  - Off-topic sources, by URL and title. SearXNG results carry no titles, so the snippet stands in.
  - FACT-style citation accuracy:
    - statement→link pairs (`factcheck.claims()`) are sampled with a per-topic seed, and the page is re-fetched;
    - the 4 chunks found by `factcheck.Passages` for the statement and its English translation are judged, with the sentence before it as context.
  - The current citation judge is judge 3 (`JUDGE` in `run.py`; 19.10.2026). Judge 1 matched chunks by numbers, Latin names and stems only, so Russian statements got the wrong chunks of English pages. It read 71% where the newer judges read about 90% on the same reports (`--rejudge`).
- `--factcheck` runs the fact-check after each research and judges the report before and after its fixes. The two judges are alike, so the gain it shows is an upper bound.
- Results go to `eval/results/<date>_<label>.md|json`, each next to the averages of the last run made the same way (same mode, writer, sources, fact-check and judge). In detailed mode the report's source list is its cited links (`DetailedReport.source_urls()`), so `in_report` is 100% there by construction.

## Run it

```bash
uv run run.py                             # every topic: quick reports (the 5–7 minute overview since 26.10.2026), Flash writes the text (≈10 ₽)
uv run run.py --topics 1,4 --label exa    # a subset, named; --mode/--writer/--sources/--pairs as in research.py
uv run run.py --topics 1 --mode detailed --writer pro   # one detailed Pro report, ≈12 ₽
uv run run.py --factcheck                 # with the fact-check: each report judged before and after its fixes
uv run run.py --rejudge 2026-10-18_2327_3.1-reader      # a past run's reports judged again by the current judge
```

- **Cost.** A run spends real money on DeepSeek: the reports plus the judges. Tell the user what a run will cost and ask before starting it; the whole budget is about 300 ₽ a month.
- **Timing.** Compare runs made off-peak. DeepSeek doubles its price 01–04 and 06–10 UTC on weekdays, and its speed changes over the day too.
- **One run at a time.** Runs share `eval/reports/`, with its library and `read_stats.json`.
- **Labels.** Name what changed in `--label`, and read the numbers against the last run made the same way, which the results file prints beside its own. Quick runs before 26.10.2026 measured the old 2–3.5 thousand-word quick report: its words, time and cost are not comparable with the overview's.
- **What gets committed.** The results (`eval/results/*.md|json`) are the record behind the numbers in `plan.md`; commit them. `eval/reports/` is gitignored.
- **Web sources** need the local SearXNG (`docker compose up -d`).
- **Checking the harness itself.** Use the mock LLM's environment (`dev/mock_llm.py`): every step runs for free, though the numbers then mean nothing.

## What eval relies on

eval imports the parts' internals in its own process, so a change there can change its numbers without failing anything:
- **worker**:
  - `pipeline` (`run_settings`, `config_file`, `preflight`, `research`, `render`);
  - `factcheck.run`, and for the citation judge `claims`, `Claim`, `Passages`, `translate`, plus `LINK`, `text_and_references` and `units` in `metrics.py`;
  - `hooks.blocked` and `hooks.SEARCH_META` (titles for the off-topic judge);
  - `suggest.fast` and `suggest.json_list` (the judges);
  - the `Added source url to research (s/3): url` log line (`metrics.ADDED`).
- **search**:
  - `sources.static_score`, for the «authoritative» share;
  - the reader's INFO lines `Read via <how>: <url>` and `Unreadable after fallbacks: <url>` («Спасено», «Не скачалось»), and gpt-researcher's fetch-failure warnings. Search must run in-process: no `SEARCH_URL`.
- **core**: `library.Library`, which stores eval's reports in its own library.
- **common**: `costs` (the meter, `is_peak`), `settings` (`MODES`, `ROOT`, `SOURCES`, `WRITERS`), `text.host`.

## eval/news: merging news into stories

- `uv run eval/news/run_dedup.py` measures the news part's merging (`my_browser/news/dedup.py`) on `eval/news/pairs.jsonl`: 103 pairs of posts from a live collection, labeled «same event» or not (digest posts null, kept out). It prints precision, recall and F1 per threshold for each model in `MODELS`, with and without shared links, and the best threshold at precision ≥ 0.95; results go to `eval/news/results/`.
- It costs nothing: no LLM, the embedding models run locally (downloaded once into `eval/reports/.models`: MiniLM 220 MB, mpnet 1 GB).
- After changing `embed.MODEL`, `embed.text()`, `collectors.link_key()` or `usable_key()`, rerun it and set `config.Settings.dedup_threshold` by the numbers. The pairs measure pairwise merging only; a merging rule that works on whole stories (`dedup._closest()`) is checked on a copy of a real `news.sqlite` instead.

## eval/opps: reading opportunities

- `uv run eval/opps/run_extract.py` measures the opportunities lane's extraction (`my_browser/news/opps.py`) on `eval/opps/cases.jsonl`: 60 real posts of the collection of 25.10.2026 (Russian and English; hackathons, competitions, internships, schools, fellowships, grants; exact, passed and missing deadlines; restrictions by country; and 16 that are not opportunities: results, meetups, lectures, jobs, memes), labeled by hand with the accepted answers.
- It runs them through `opps.describe()` and `opps.read()`, with today fixed at 25.10.2026, and the deadline through `opps.deadline()`: the prompt and the parsing the loop uses. It prints the share right of «is an opportunity» over all cases, and over the opportunities the deadline's day (none when the text gives none), `ru_access`, the type and `closed`; a missed opportunity misses every field. Results go to `eval/opps/results/<date>_<time>_<label>.md|json` (the json has every answer).
- It costs about 1 ₽ a run (real Flash, 10 calls); ask the user before running it. Its spend goes to a throwaway `REPORTS_DIR`, never to the app's budget.
- After changing `opps.EXTRACT_PROMPT`, `describe()` or `deadline()`, rerun it and read the misses, not just the shares: a label may be the thing to fix.

When the citation judge's inputs change (`factcheck.claims`, `Passages`, `translate`, `common.text.MARKDOWN_LINK`), bump `JUDGE` and rejudge the last run (`--rejudge`) so that the numbers stay comparable.

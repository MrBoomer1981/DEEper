# CLAUDE.md: search

Guidance for changing `my_browser/search/`: what the worker and eval expect from search, and what search expects from them. What search does is in the root CLAUDE.md; paths below are relative to `my_browser/` unless they are repo-root files.

## What others rely on

**The worker** (`worker/hooks.py`) is the only caller of the API, in-process unless `SEARCH_URL` is set:
- `POST /select {query, candidates: [{url, title, snippet}], domain_counts: {domain: n}, blocked: [domain]}` → `{selected: [[url, score]], usage}`, best first.
  - The worker marks the selected URLs visited and logs each with its score.
  - `usage` is `Meter.by_model` and goes into the research's cost.
  - It is called once per batch of search results, with a 120 s timeout. An error loses that sub-query's sources.
- `POST /read {url}` → `{url, raw_content, title, image_urls}`.
  - This is the shape of gpt-researcher's `Scraper.extract_data_from_url`, and it is handed back to gpt-researcher as is.
  - `raw_content` is `None` when nothing could read the page.
  - The worker also keeps every `raw_content` for the fact-check until the research ends (`hooks.read_pages`), so the fallbacks' 150k cap also bounds the worker's memory.
  - It is called for every page. `read()` gives the plain fetch 30 s and the fallbacks 45 s (`DIRECT_SECONDS`, `RESCUE_SECONDS`), because a batch of pages waits for its slowest one; the worker's own 180 s timeout applies only when search runs apart (`SEARCH_URL`), since an in-process call never leaves the process. An error loses only that page.

**eval** relies on search's internals:
- `eval/metrics.py` imports `sources.static_score` for the «authoritative» share. Editing `DOMAIN_SCORES` or `TOP_TIER_HOST` changes that metric, so later runs stop being comparable on it.
- It counts pages from log lines:
  - rescued and lost pages from the reader's INFO lines `Read via <how>: <url> (<n> chars)` and `Unreadable after fallbacks: <url>` (logger `my_browser.search.reader`);
  - failed plain fetches from gpt-researcher's scraper warnings, which are also logged inside `reader.read()`.
  - Its log handlers live in eval's own process, so eval needs search in-process: run it without `SEARCH_URL`.
- eval sets its own `REPORTS_DIR`, so it keeps its own `read_stats.json` in `eval/reports/`. Skipping and demotion in eval depend on eval's history, not the app's.

## What search relies on

- **The worker's input**:
  - titles and snippets come from the worker's `SEARCH_META` (the retrievers' results);
  - candidates arrive deduplicated, made readable (`readable_url`) and without already visited URLs;
  - `domain_counts` and `blocked` are keyed by `common.text.host()`, like search's caps and tiers. Keep both sides on `host()`.
- **gpt-researcher** (the `search` group):
  - `Scraper(urls, user_agent, scraper_name, worker_pool)` and its `.session`;
  - `WorkerPool` (`reader._scraper()` replaces its `executor` with a shared pool);
  - `validate_url` and `UnsafeURLError`;
  - `Config` and `create_chat_completion` for the rating.
- **The unpatched plain fetch**:
  - It is `common.gptr.ORIGINAL_EXTRACT`, the original `Scraper.extract_data_from_url`, called as `ORIGINAL_EXTRACT(scraper, url, scraper.session)`.
  - In the all-in-one process the worker has replaced that method with a call to search. Calling `scraper.extract_data_from_url()` here would loop back through the API.
- **Event loops**:
  - In-process, every research runs search on its own event loop; as a server, one loop serves everything.
  - Anything loop-bound (semaphores, clients, sessions) needs a per-loop key, as `_scraper()` has; it cannot be cached at module level. httpx clients are opened per call.
- **Costs**:
  - `api.py` imports `common.costs`, which meters LLM calls in this process. The rating runs inside `costs.metered()` and returns its usage.
  - Any new LLM call needs the same treatment. In-process, the research's meter is in the context and would count the call directly; in a separate process it would be lost; the two modes would then disagree.
- **Configuration**:
  - The rating reads `config.json` and the environment through `Config(CONFIG_PATH)` (`FAST_LLM`, `LLM_KWARGS`), not the research's per-run config.
  - A separate search process therefore needs `DEEPSEEK_API_KEY` and the same `config.json`.
- **Free external services**:
  - Europe PMC and OpenAlex, both without a key;
  - Jina Reader: `JINA_API_KEY` is optional; without it the limit is 20 requests a minute, with a free key 500.

## State search owns

- `REPORTS_DIR/read_stats.json` holds per-domain `direct_ok`, `direct_fail`, `rescued` and `lost`.
  - It is loaded once per process and rewritten after every page, so run one search process per `REPORTS_DIR`; two would overwrite each other's counts.
  - It lives next to core's reports; core's `sync()` skips it by name.
  - Deleting it resets skipping and demotion.
- `jina_pace` is per process: two search processes without a key would exceed Jina's limit.

## Coming changes

The search engines still run in the worker: gpt-researcher's retrievers, with `hooks.py` capturing titles and snippets. With Exa (plan 3.4) they move here. Then `SEARCH_META`, the retriever patch and the input of `/select` change together with `worker/hooks.py`.

## When you change…

| Change | Also update |
|---|---|
| a request or response of `/select` or `/read` | `worker/hooks.py` (`_select`, and `_read_via_search` with its empty result) |
| `DOMAIN_SCORES`, `TOP_TIER_HOST` | mention it in the eval label: «authoritative» shifts |
| the reader's log lines | `eval/metrics.py` (`RESCUED`, `LOST`) |
| selection rules or caps (`MAX_SOURCES_PER_QUERY`, `PER_DOMAIN_QUERY`, `PER_DOMAIN_RESEARCH`, also env overrides) | measure with `uv run eval/run.py` against the last run made the same way |
| the format of `read_stats.json` | only `reader.py` and `sources.py` read it; the file already on the volume must still load |

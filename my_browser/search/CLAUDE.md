# CLAUDE.md: search

Search picks the research's sources from the search results (`/select`) and reads pages, with free fallbacks when a site does not let the plain fetch in (`/read`). This file covers what it does, how to run and check it alone, what the worker and eval expect from it, and what it expects from them. The repository's rules for working in one part are in the root CLAUDE.md. Paths below are relative to `my_browser/` unless they are repo-root files.

## What search does

- `search/sources.py` (`/select`):
  - One FAST_LLM call per batch scores every candidate 0–3 for relevance to the topic, credibility and freshness, seeing URL, title and snippet.
  - Static tiers (`DOMAIN_SCORES`, gov/edu) only raise a relevant source and never rescue an off-topic one; arXiv's keyword search returns plenty of unrelated physics.
  - Selection:
    - 0 is dropped;
    - 1 is kept only if the batch has nothing ≥2, and then at most 3;
    - at most `PER_DOMAIN_QUERY`=2 per domain per batch and `PER_DOMAIN_RESEARCH`=5 per research;
    - at most `MAX_SOURCES_PER_QUERY`=8;
    - domains that stay unreadable lose a point.
- `search/reader.py` (`/read`):
  - First the plain fetch, which is the unpatched gpt-researcher scraper. Then the free fallbacks, in order:
    1. Europe PMC full text, for PubMed Central links;
    2. an open-access copy found via OpenAlex, for URLs that carry a DOI;
    3. Jina Reader (`r.jina.ai`), paced to 20 requests a minute unless `JINA_API_KEY` is set;
    4. the OpenAlex abstract.
  - Fallback text is capped at 150k characters.
  - A page gets 30 s for the plain fetch and 45 s for the fallbacks: a batch of pages waits for its slowest one, and one slow PDF once held a batch for 4 minutes.
  - gpt-researcher's `WorkerPool` holds an `asyncio.Semaphore`, which binds to one event loop, so each loop gets its own `Scraper`; they share one thread pool.
  - Per-domain outcomes go to `REPORTS_DIR/read_stats.json` (search's own state). A domain whose plain fetch failed at least 80% of 3+ tries skips it.
  - Its INFO lines (`Read via <how>: <url>`, `Unreadable after fallbacks: <url>`) feed eval's «Спасено» and «Не скачалось». Upstream's own «treating as fetch failure» warning fires before the fallbacks run, so it does not mean the page was lost.
- `search/api.py`: the FastAPI app with `/select`, `/read` and `/health`, every route behind the service token.

## Run and check it alone

```bash
uv run python -m my_browser.search    # API at 127.0.0.1:8503 (SEARCH_PORT)
```

Then call it with the service token of the same `REPORTS_DIR`. `T` is the token, `$(cat <REPORTS_DIR>/.service_token)`:

```bash
curl -s localhost:8503/read -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"url": "https://en.wikipedia.org/wiki/Sodium-ion_battery"}'
curl -s localhost:8503/select -H "Authorization: Bearer $T" -H 'Content-Type: application/json' \
  -d '{"query": "натрий-ионные аккумуляторы", "candidates": [{"url": "https://en.wikipedia.org/wiki/Sodium-ion_battery", "title": "Sodium-ion battery", "snippet": "A sodium-ion battery is a rechargeable battery..."}]}'
```

- `/read` uses no LLM. `/select` calls FAST_LLM, a fraction of a kopeck per batch, or the mock with its environment (`dev/mock_llm.py`).
- A search process writes `read_stats.json` in its `REPORTS_DIR`. Experiment with a throwaway one, so the app's stats stay as they are.
- **A research through a search started apart**: run the worker with `SEARCH_URL`. The all-in-one app and `research.py` always call search in-process.
- **Selection, caps, tiers and reading** are measured with `eval/run.py`, about 10 ₽ for the quick set. eval runs search in-process and counts pages from the reader's log lines.
- **The whole project.** Before committing, run the `check-project` skill.

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

**`dev/mock_llm.py`** answers the rating prompt by its phrase «Return ONLY a JSON array» (every candidate gets 3). Reword it, and free runs get the mock's generic text instead of ratings.

## What search relies on

- **The worker's input**:
  - titles and snippets come from the worker's `SEARCH_META` (the retrievers' results);
  - candidates arrive deduplicated, made readable (`readable_url`) and without already visited URLs;
  - `domain_counts` and `blocked` are keyed by `common.text.host()`, like search's caps and tiers. Keep both sides on `host()`.
- **gpt-researcher** (the `search` group; the worker's group has it too, so an upgrade changes both parts):
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
  - Jina Reader: `JINA_API_KEY` is optional; without it the limit is 20 requests a minute, with a free key 500. The key is read from the environment (`.env` locally, the service's variables on Railway).

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
| the rating prompt | `dev/mock_llm.py`, if its phrase «Return ONLY a JSON array» goes |
| selection rules or caps (`MAX_SOURCES_PER_QUERY`, `PER_DOMAIN_QUERY`, `PER_DOMAIN_RESEARCH`, also env overrides) | measure with `uv run eval/run.py` against the last run made the same way |
| the format of `read_stats.json` | only `reader.py` and `sources.py` read it; the file already on the volume must still load |

# CLAUDE.md: common

Guidance for changing `my_browser/common/`. This is code shared by the parts, so every change here changes several of them at once. What lives here is in the root CLAUDE.md; paths below are relative to `my_browser/` unless they are repo-root files.

## Who relies on what

**`settings.py`**: used by every part, `research.py` and `eval/`.
- Values are computed at import:
  - `.env` is loaded, but real environment variables win;
  - `REPORTS_DIR`: eval sets its own before importing anything from `my_browser`;
  - `WEB_RETRIEVER`, `PORT`, `CORE_URL` (derived from `PORT`), `WORKER_URL`, `SEARCH_URL`.
- The keys of `SOURCES`, `WRITERS` and `MODES` are repeated as labels in `core/ui/kit.py` and the bot. `FACTCHECK_MODES` (modes checked by default) is read by core's service, the search screen's sheet (`core/ui/search.py`), the bot and `research.py`.
- Each entry point calls `configure_logging()`.

**`clients.py`**: used by core (to reach the worker), the worker (core, search) and the bot (core).
- `call()` raises on 4xx and 5xx, and callers rely on it: the bot treats a 404 as "no such report".
- An in-process call imports the callee's API module (`_APPS`) into the caller's process, with all its dependencies.
- It opens a new client per call; a shared one would bind to the first event loop.

**`auth.py`**: used by the APIs of core, the worker and search.
- They check a bearer service token.
- `service_token()` is `SERVICE_TOKEN`, or else the `REPORTS_DIR/.service_token` file. Processes on different machines, or with different `REPORTS_DIR`, need `SERVICE_TOKEN`.

**`costs.py`**: used by the worker (pipeline, job loop, API, suggest, ask), search's API, `research.py` and eval.
- Importing it patches `GenericLLMProvider.get_chat_response` in that process.
- `PRICES` is keyed by model name. A model missing from it is counted in `unpriced_calls`, not in rubles.
- `Meter.by_model` is the wire format of search's usage (`/select` → `Meter.merge`).
- `Meter.summary()` becomes the `cost` of each report's JSON; core and both UIs read its `rub`.

**`text.py`**:
- `host()` (core's blocklist, search, the worker's hooks, eval) is the domain key everywhere: blocklist entries, per-domain caps and tiers, `read_stats.json`, eval's metrics. Changing its normalization, for example keeping `www.`, makes existing blocklist entries and stats silently stop matching.
- `readable_url()` (the worker's hooks) produces the keys of `SEARCH_META` and `visited_urls`.
- `slugify()` (core's library) names new report files only; old names stay valid.
- `rub_text()` is used by the web UI, the bot and `research.py`; `plural()` by the web UI and the worker's fact-check section.
- `MARKDOWN_LINK` finds `[anchor](url)` with URLs that hold parentheses («…_(рынок_России)»). The fact-check's claims (and so eval's citation pairs) and the detailed report's reference list use it; changing it changes eval's sampled pairs, so bump `JUDGE` in `eval/run.py`.

**`report.py`**: used by the worker (`section`, `KINDS`, `ANY_ANSWER`, `body`, `FACTCHECK_TITLE`, `VERDICT_TEXT`), the web UI (`body`, `appended`, `KINDS`, `ANY_ANSWER`, `FACTCHECK_TITLE`, `VERDICT_TEXT`), the bot (`KINDS`, `ANY_ANSWER`) and eval (`body`, through the fact-check's helpers).
- `FOLLOWUPS_TITLE` and `FACTCHECK_TITLE` are in saved reports, so `body()` and `appended()` must keep recognizing the titles of reports already on the volume. The fact-check section always comes before the follow-ups.
- `VERDICT_TEXT` keys are the lower-case verdicts the worker writes into `factcheck.doubtful`.
- The `KINDS` keys are the `kind` values the follow-ups prompt asks for and `suggest.followups()` accepts.
- `ANY_ANSWER` is dropped from the model's options and added back by both UIs.

**`gptr.py`**: used by the worker's hooks and search's reader.
- It must be imported before anything patches `Scraper.extract_data_from_url`; `worker/hooks.py` imports it first.
- If the worker patches another upstream function that search needs unpatched, capture it here the same way.

## Dependencies

- `[project]` in `pyproject.toml` holds only httpx and python-dotenv, which every part installs.
- `costs.py` and `gptr.py` import gpt-researcher, and `auth.py` imports fastapi. Only the parts that have those packages import these modules: the worker and search, plus core, which gets fastapi with NiceGUI.
- The bot uses only `clients`, `report`, `settings` and `text`, so a bot-only install stays small. Keep these four free of heavy imports.

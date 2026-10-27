# CLAUDE.md: common

`my_browser/common/` is the code every part shares, so every change here changes several of them at once. This file covers what lives here, who relies on what, and how to check a change. The repository's rules for working in one part are in the root CLAUDE.md. Paths below are relative to `my_browser/` unless they are repo-root files.

## What lives here

- `settings.py`:
  - paths: `ROOT`, `CONFIG_PATH`, `REPORTS_DIR`;
  - research options: `SOURCES`, `WRITERS`, `MODES`, `DEFAULT_MODE` (`quick`, the 5–7 minute overview, since 25.10.2026), `FACTCHECK_MODES` (every mode);
  - how the parts find each other: `PORT`, `CORE_URL`, `WORKER_URL`, `SEARCH_URL`, `NEWS_URL`, `service_token()`;
  - `configure_logging()`, which quiets httpx because the job loop polls core every few seconds.
- `clients.py`: `call(part, method, path)` and the blocking `call_core()`, in-process or over HTTP (see the root CLAUDE.md).
- `auth.py`: `require_token`, the service token check of the parts' APIs.
- `costs.py` meters real cost.
  - It wraps `GenericLLMProvider.get_chat_response` and adds usage tokens to the `Meter` in the `costs.current` ContextVar. The job loop sets it per research; `costs.metered()` isolates a single call (a question, a clarification, search's rating, which is returned as usage and merged into the job's meter).
  - DeepSeek prices (peak 01–04 and 06–10 UTC on weekdays, half price otherwise) and the CBR USD/RUB rate (cached 12 h; `USD_RUB` overrides) go into `cost` in each report's JSON and into `cost_rub` per Q&A. The UI and the bot show them.
  - Streamed calls report usage only because `LLM_KWARGS.stream_usage` is true.
  - `finish_reason == "length"` is logged and counted as `truncated_calls`.
- `text.py`: `host`, `readable_url`, `slugify`, `rub_text`, `plural`, `MARKDOWN_LINK`, `CITATIONS` (a parenthesized group of citation links: core's pages turn it into numbers, the bot's «главное» drops it), `READING_WPM` (180 words a minute) and `reading_minutes()`.
  - `readable_url` decodes percent-encoded Cyrillic and keeps ASCII escapes. Encoded slugs cost 3–4 tokens per letter, and they made long reports hit the output cap mid-reference (16k back then, now 32k).
- `report.py`: the sections appended to a report, «Проверка фактов» and «Что исследовать дальше», with the verdict wording; the follow-up kinds (`KINDS`, the ones asked for now in `FOLLOWUP_KINDS`); the overview's sections and the length asked of the writer (`OVERVIEW_HEADINGS`, `OVERVIEW_WORDS`, `OVERVIEW_MIN`, `OVERVIEW_MAX`; Pro writes less than asked, see `worker/CLAUDE.md`); `summary()`, a report's key points without links (its «Главное», else its conclusion, else its opening), `without_references()`, the text without the list of sources at its end (what the screens show), and `reading_words()`, the words a reader reads (no appended sections, sources or citations).
- `phases.py`: a running job's phase, the line in which the worker says what it is doing. The worker writes it and core's job page parses it, both from this module.
- `gptr.py`: gpt-researcher's unpatched `Scraper.extract_data_from_url`, captured before the worker patches it, so that search can still fetch pages in the same process.
- `memory.py`: hands freed memory back to the system after heavy work. `trim()` calls glibc's `malloc_trim(0)` (a no-op without glibc, so on macOS); `release()` collects cyclic garbage first. On Railway the process climbed to about 1 GB within hours of a deploy and stayed there: glibc keeps freed pages. On Linux, threads that read page-sized texts and dropped them left a process at 1.2 GB, and `malloc_trim(0)` brought it to 15 MB; `MALLOC_ARENA_MAX=2` alone changed nothing there, so the Dockerfile does not set it.

## Run and check

Nothing here runs on its own. Check a change through the parts:
- The `check-project` skill imports every module and runs one research end to end, through the queue, the worker and search.
- When you change what existing code does, also check each user listed below, with the commands in its folder's CLAUDE.md («Run and check it alone»).

Adding a function, a constant or an optional argument is safe. Renaming or removing one, or changing what it returns, changes every part that uses it: update them in the same commit.

## Who relies on what

**`settings.py`**: used by every part, `research.py` and `eval/`.
- Values are computed at import:
  - `.env` is loaded, but real environment variables win;
  - `REPORTS_DIR`: eval sets its own before importing anything from `my_browser`;
  - `WEB_RETRIEVER`, `PORT`, `CORE_URL` (derived from `PORT`), `WORKER_URL`, `SEARCH_URL`, `NEWS_URL`.
- The keys of `SOURCES`, `WRITERS` and `MODES` are repeated as labels in `core/ui/kit.py` and the bot. `FACTCHECK_MODES` (modes checked by default) is read by core's service, the search screen's sheet (`core/ui/search.py`), the bot and `research.py`.
- Each entry point calls `configure_logging()`.
- `PORT` also makes core public (`core/ui/kit.PUBLIC`, unless `PUBLIC=0`).

**`clients.py`**: used by core (to reach the worker), the worker (core, search) and the bot (core). `_APPS` also maps `news` (`NEWS_URL`), for the bot and core from the news part's stages 1 and 6.
- `call()` raises on 4xx and 5xx, and callers rely on it: the bot treats a 404 as "no such report".
- An in-process call imports the callee's API module (`_APPS`) into the caller's process, with all its dependencies.
- It opens a new client per call; a shared one would bind to the first event loop.

**`auth.py`**: used by the APIs of core, the worker, search and news.
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
- `rub_text()` is used by the web UI, the bot and `research.py`; it puts a no-break space before «₽», so the sign never wraps alone on a narrow screen. `plural()` is used by the web UI, `phases.py` and the worker's fact-check section.
- `MARKDOWN_LINK` finds `[anchor](url)` with URLs that hold parentheses («…_(рынок_России)»). The fact-check's claims (and so eval's citation pairs) and the detailed report's reference list use it; changing it changes eval's sampled pairs, so bump `JUDGE` in `eval/run.py`.

**`report.py`**: used by the worker (`section`, `KINDS`, `FOLLOWUP_KINDS`, `OVERVIEW_*`, `ANY_ANSWER`, `body`, `FACTCHECK_TITLE`, `VERDICT_TEXT`), core (`summary` for a continuation's branch, `reading_words` in the library and the report page, `OVERVIEW_MIN` for the job page's writing percent, `body`, `appended`, `KINDS`, `ANY_ANSWER`, `FACTCHECK_TITLE`, `VERDICT_TEXT`), the bot (`KINDS`, `ANY_ANSWER`) and eval (`body`, through the fact-check's helpers).
- `OVERVIEW_HEADINGS` are the headings the writer is told to use; `summary()` and the bot's `_summary()` look for «Главное» by name, so a change of wording changes all three.
- `FOLLOWUPS_TITLE` and `FACTCHECK_TITLE` are in saved reports, so `body()` and `appended()` must keep recognizing the titles of reports already on the volume. The fact-check section always comes before the follow-ups.
- `VERDICT_TEXT` keys are the lower-case verdicts the worker writes into `factcheck.doubtful`.
- The `KINDS` keys are the `kind` values the follow-ups prompt asks for and `suggest.followups()` accepts.
- `ANY_ANSWER` is dropped from the model's options and added back by both UIs.

**`phases.py`**:
- Writers: the worker (`runner.py`, `pipeline.py`, `detailed.py`, `factcheck.py`).
- Reader: core's `ui/progress.py`, which turns a line into the job page's percent and status (`percent()`, `status()`).
- The bot sends the line to the chat as it is, and `research.py` prints it.
- A phase with a counter has its pattern right under the function that writes it; change both together.
- A line core cannot read only loses its percent: nothing fails, so check a changed phase through `percent()` and `status()`.
- Core's own queue states («В очереди», «Прервано», «Готово», «Остановлено») are not phases of the worker; they live in `core/jobs.py`.

**`memory.py`**: used by the worker's job loop (`release()` after each research) and the news loop (`trim()` after a collection, `release()` after the merging and scoring run).
- `release()`'s `gc.collect()` holds the GIL for a moment, which pauses the web app too: call it after heavy work, not on every small step. `trim()` runs in ctypes without the GIL.
- It imports only the standard library, so any part may use it.

**`gptr.py`**: used by the worker's hooks and search's reader.
- It must be imported before anything patches `Scraper.extract_data_from_url`; `worker/hooks.py` imports it first.
- If the worker patches another upstream function that search needs unpatched, capture it here the same way.

## Dependencies

- `[project]` in `pyproject.toml` holds only httpx and python-dotenv, which every part installs.
- `costs.py` and `gptr.py` import gpt-researcher, and `auth.py` imports fastapi. Only the parts that have those packages import these modules: the worker and search, plus core, which gets fastapi with NiceGUI.
- The bot uses only `clients`, `report`, `settings` and `text`, so a bot-only install stays small. Keep these four free of heavy imports, and `phases` too (core and the worker import it, and it imports only `text`).

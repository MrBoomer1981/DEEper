# CLAUDE.md: core

Guidance for changing `my_browser/core/`: what the other parts expect from core and what core expects from them. What core does is in the root CLAUDE.md; paths below are relative to `my_browser/` unless they are repo-root files.

## What others rely on

**Worker** (`worker/runner.py`, over HTTP with `call_core`) uses the queue endpoints:
- `POST /api/worker/hello {worker}`: every running job of another worker id becomes interrupted.
- `POST /api/worker/claim {worker}`: 204 when nothing is queued, otherwise the job plus `blocked` (the blocklist). The worker reads `id`, `query`, `mode`, `sources`, `writer`, `parent`, `factcheck`, `blocked`.
- `POST /api/worker/jobs/{id}/progress {worker, phase, queries, urls, log, cost_rub}`: also the heartbeat (`HEARTBEAT_TIMEOUT` is 5 min, the worker sends at least every 30 s). It is ignored unless the job is running under that worker. `urls` are `[url, "3/3"]` pairs.
- `POST /api/worker/jobs/{id}/result {worker, files}`: `files` maps `.md`, `.json` and, when rendering worked, `.docx` and `.pdf` to base64. Accepted even after the job was given up as interrupted. The worker retries six times over about two minutes; a retry after a lost response would store the report twice, since nothing deduplicates by job id.
- `POST /api/worker/jobs/{id}/error {worker, error}`.

**Bot** (`bot/telegram.py`) uses the rest of `/api` and reads these fields; a renamed field breaks a handler at run time, not at import:
- job: `id`, `active`, `status`, `phase`, `elapsed`, `error`, `report_id`, `query`, `mode`, `sources`, `factcheck`, `origin_ref`;
- `POST /api/jobs` takes `factcheck` as true, false or absent; absent means the mode decides (`FACTCHECK_MODES`), resolved in `service.submit()` and stored as a bool;
- `GET /api/reports/{id}` (`service.report_view`): `entry` (`id`, `query`, `words`, `sources_count`, `duration_sec`, `name`), `meta` (the report JSON as stored), `markdown`, `qa`;
- `GET /api/reports` (plus `snippet` when searching with `q`), `GET /api/reports/{id}/files/{pdf|docx|md}` (404 for a missing file; the bot sends what exists), `DELETE /api/reports/{id}` → `deleted`;
- `POST /api/reports/{id}/ask` → the stored Q&A item (`a`, `cost_rub`, `research`); `/api/clarify` → `questions`, `cost_rub`; `/api/refine` → `topic`, `cost_rub`; blocklist → `domains`, `domain`, `removed`;
- `GET /api/jobs?status=interrupted&origin=telegram` lists only jobs the bot has not reported yet; it then calls `POST /api/jobs/{id}/notified`.
- The bot addresses follow-up topics and Q&A research topics by position (`f:<report>:<i>`, `r:<report>:<i>`; Telegram limits callback data to 64 bytes). Keep `meta.followups` in its order and `.qa.json` append-only.

**In-process users**: `research.py` and `eval/run.py` call `Library().store(files)` directly, without the API or the queue, and `research.py` also calls `blocklist.blocked_domains()`. `app.py` imports `core.web` and registers the job loop and the bot on `web.app` (NiceGUI's app).

## What core relies on

- **The worker's API**, through `common.clients.call("worker", ...)` in `service.py`; request and response shapes are in `worker/CLAUDE.md`. With `WORKER_URL` unset it runs inside core's process and imports `my_browser.worker.api`, and gpt-researcher with it. So core installed alone (`--group core`) needs `WORKER_URL`.
- **The report files written by the worker's `pipeline.render()`**:
  - `Library.add` requires `query`, `mode`, `created`, `words`, `duration_sec` and `sources` (a list); `parent` is optional. `store_result` reads `cost.rub`.
  - The pages read `writer`, `cost`, `followups`, `followups_cost_rub`, `sources` and `factcheck` (`checked`; `doubtful` items with `raw`, `verdict`, `note`). Reports already on the volume predate some fields, so read newer ones with `.get()`.
  - The Markdown ends with «Проверка фактов» (when checked), then «Что исследовать дальше»; `common.report.body()` cuts both off for the page, and `appended()` gives the fact-check text for its block.
  - `mark_doubtful()` finds each doubtful sentence by its `raw` text (whitespace-tolerant) to put the «?» after it, so a worker change to how claims are cut out of the text shows up here as missing marks.
- **`common`**: `settings`, `auth`, `report`, `text`, `clients`. Core imports no module of the worker, search or bot.

## State core owns

All of it is in `REPORTS_DIR` (the Railway volume), next to search's `read_stats.json`:
- `library.sqlite`: tables `reports` and `reports_fts` (`library.py`) and `jobs` (`queue.py`), each created on first use; a column added to `jobs` later goes into `ADDED_COLUMNS`, so older databases on the volume get it. `sync()` rebuilds the report tables from the files; the queue cannot be rebuilt, so deleting the database also drops queued and finished jobs.
- `<date>_<time>_<slug>.{md,json,docx,pdf}` and `<name>.qa.json`. `sync()` indexes only `<date>_*.json` files that are not `.qa.json`, so other JSON in the directory is left alone.
- `blocked_domains.txt` (eval reads it straight from `reports/`), `.service_token` (when `SERVICE_TOKEN` is unset, every part with the same `REPORTS_DIR` reads it), `.storage_secret` and `.nicegui/` (logins).

## Constraints

- One core process per `REPORTS_DIR`: the queue's write lock is per process (Railway runs one replica).
- One worker: `hello()` interrupts the running jobs of every other worker id, so a second worker starting would cut off the first one's research. Several workers would need a hello that gives up only the restarted worker's own jobs, leaving the rest to the heartbeat.
- `web.py` sets `NICEGUI_STORAGE_PATH` before importing NiceGUI, which reads the variable at import. Import NiceGUI only through `core.web` (as `app.py` does), and the screens in `core/ui/` likewise only from `web.py`; otherwise logins live outside the volume and every redeploy logs everyone out.
- Styles live in `static/app.css`. Quasar puts its own `!important` color classes (`bg-primary`, `text-white`) in a CSS cascade layer, and an `!important` declaration inside a layer beats one outside it, so app.css cannot override a component's color: give it through props (`color=accent text-color=night`), as `ui/kit.primary()` does. Avoid class names Quasar already uses (`.row`, `.column`, `.col`…): its `.row` wraps flex children.
- `PORT` also switches on public mode: bind to 0.0.0.0 and require `APP_PASSWORD` (Railway sets `PORT`). The worker and the bot find core at `CORE_URL`, by default `http://127.0.0.1:$PORT`.
- `OPEN_PATHS` must keep `/api/` open to the login middleware: the bot and the worker carry the service token, not a login.
- The API does not validate `mode`, `sources` and `writer`. An unknown key is queued and then fails in the worker (`SOURCES[...]`, `WRITERS[...]`), and `MODES[job["mode"]]` in the pages and `MODE_BUTTONS[...]` in the bot raise on it.
- The pages call `service` and `queue` directly, the bot goes through the API: an operation both need belongs in `service.py`, with a thin route in `api.py`.

## When you change…

| Change | Also update |
|---|---|
| a job field or status | the bot (`Status.follow`, `finish`, `notify_interrupted`), the job page in `web.py`, `worker/runner.py` if it sends or reads the field |
| an `/api` route or response field | the bot; for `/api/worker/*` also `worker/runner.py` |
| what the pages read from the report JSON | it is written by `worker/pipeline.render()`; old reports lack newer fields |
| a mode, source or writer key | `common/settings.py` (also `FACTCHECK_MODES`); `web.py` (`MODES`, `MODE_HINTS`, `WRITER_LABELS`, `WRITER_HINTS`, `WRITER_OF_MODEL`); the bot (`MODE_BUTTONS`, `SOURCE_BUTTONS`, `HELP`); `worker/suggest.FOLLOWUP_MODES` |
| what the report page shows of the fact-check | it comes from `worker/factcheck.summary()` and `section()`; the bot's `_factcheck_line()` reads the same counts |
| the time estimates of the modes | `web.MODE_HINTS` and the bot's `HELP` |
| report file naming (`Library.store`, `common.text.slugify`) | `REPORT_NAME` in `library.py`; parents are stored by file name |

# CLAUDE.md: core

Core is DEEper's web app (NiceGUI), its API under `/api`, the report library and the job queue. This file covers what core does, how to run and check it alone, what the other parts expect from it and what it expects from them. The repository's rules for working in one part are in the root CLAUDE.md. Paths below are relative to `my_browser/` unless they are repo-root files.

## What core does

- `core/jobs.py`: jobs in SQLite (`jobs` table in `library.sqlite`).
  - Statuses go queued → running → done | error | interrupted.
  - The worker claims the oldest queued job and reports progress (phase, queries, accepted sources with scores, the last 200 log lines, cost), which doubles as its heartbeat.
  - A worker that (re)starts says hello, and jobs running under another worker id become interrupted; a running job silent for 5 minutes is interrupted too. Interrupted and failed jobs go back to the queue as they were (`restart`).
  - Telegram jobs keep `origin_ref` (chat, status message), so the bot can update that message after a restart.
  - Each job carries `factcheck`; when the bot or the API leave it out, `service.submit()` takes it from the mode (`FACTCHECK_MODES`: every mode since 25.10.2026).
  - A follow-up started by one tap is held back `HOLD_SEC` (5 s, `not_before`): `claim()` skips it until then, and the job page and the bot offer «Отменить». `cancel()` deletes a job that has not started (it is gone afterwards: `get()` gives None, the API 404).
  - A job may carry `route_step` (the step of its parent's route it is) and `profile` (the reader's interests from «Фокус»). While it runs, its progress carries `draft`, the report's text written so far; `finish()` clears it.
  - `typical_cost(mode, writer, factcheck)`: the median cost of the last finished jobs made the same way, for the follow-up cards' price.
  - Columns newer than an existing `jobs` table are added on first use (`ADDED_COLUMNS`).
- `core/service.py`: operations shared by the pages (direct calls) and `core/api.py` (bot, worker): submit, claim, store a finished report (`Library.store`, base64 files from the worker), report view, and the worker-backed `ask` / `followups` / `clarify` / `refine`.
  - Branches: `lineage(entry)` is a report and the ones it continues (by `parent`, nearest first, with their JSON); `route(chain)` the route its first report proposed, if any (`steps` with that report's topic as step 1, `at` the step chain[0] is or None off the route, `reached`, `next`); `branch(chain)` the nearest `BRANCH_REPORTS` (4) reports' topics and key points (`common.report.summary()`).
  - `claim()` (async) adds the blocklist and, for a job that continues a branch, `branch`, `route` (`{steps, at}`) and `profile`: the job's «Фокус» interests plus the profile the user wrote with the bot's `/profile` (the news part's `GET /profile`, without contacts; "" when it is the default or news does not answer).
  - `followups(entry, interests)` gives an older report its topics (and a route, if it starts a branch) the same way.
- `core/web.py` (NiceGUI 3) sets the app up and imports the screens:
  - the head (manifest, iPhone meta, font preloads, `app.css`/`app.js` and the icon font with a `?v=` content hash of all three, `asset_version()`);
  - static files with a 7-day cache and `/files/{id}/{pdf|docx|md}` for downloads;
  - auth: `AuthMiddleware` is active when it listens publicly (`kit.PUBLIC`: `PORT` is set, as on Railway, unless `PUBLIC=0`) or when `APP_PASSWORD` is set; `/api/` is open to it and checks the service token itself;
  - `NICEGUI_STORAGE_PATH` and the storage secret in `reports/` (the volume), so logins survive redeploys;
  - font preloads: Onest, Unbounded, JetBrains Mono and the icons. A browser fetches a font only once some text needs it, so without the preload every new screen showed its icons and mono labels a moment late.
  - the icons: `static/fonts/icons.woff2`, NiceGUI's Material Icons cut by `dev/icons.py` to the icons the code names plus Quasar's own set (98 of 2 229: 5 KB instead of 129 KB, a fifth of a first visit's weight; 25.10.2026). The head declares it as «DEEper Icons» with its `?v=`, and app.css points Quasar's `.material-icons` at it, so NiceGUI's full font is never fetched. An icon is a ligature: a name the cut font lacks shows up as the word itself. `.woff2` is registered as `font/woff2`, which the slim Docker image did not know;
  - `ui.run(tailwind=False, reconnect_timeout=60)`: every screen is styled by `static/app.css`, and a phone that switched apps for a minute comes back to the same page.
- `core/ui/`: one module per screen, mobile first (`DESIGN_HANDOFF.md`), with a bottom tab bar «Поиск · Радар · Библиотека · Фокус».
  - `tabs.py`: the tab bar's four sections are **one page**, registered at `/`, `/radar`, `/library` and `/focus`. Each address builds all four sections (`search_screen()`, `radar_screen()`, `library_screen()`, `focus_screen()`) with its own one shown.
    - app.js switches sections in the browser: at once, with an entrance animation, keeping the address (`history.pushState`), «Назад» and each section's scroll position. A tap on the current section scrolls it to the top. A page per section had cost a round trip and a new page at every tap, 0.6–0.9 s from Russia.
    - Showing a section again sends `tab_shown`. The section's `on_show` redraws only what changed since it was drawn («Библиотека»: `snapshot()` of reports, researches and bookmarks; «Радар»: the open tab's news and topics, or its opportunities), so lists do not blink.
    - «Поиск» follows «Фокус» on the same page: `kit.save_prefs()` calls the callbacks registered with `kit.on_prefs_change()` for this client.
    - A link with a query (`/?topic=…` from a follow-up) still loads the page, which the search section needs.
  - `kit.py`: what the screens share (the shell with the tab bar, bottom sheets, list rows, buttons, the mascot, the graffiti layer, labels such as `MODES`, `MODE_HINTS`, `WRITER_LABELS`) and the user's defaults from «Фокус» (`prefs()`, per browser in `app.storage.user`; `PREFS_VERSION` 2 dropped once a mode chosen before the overview became the default, and made a preset of the detailed report «Мой»). `start_followup()` and `followup_sub()` are the follow-up cards' one tap and caption, shared by the report page and «Радар». `kit.markdown()` turns Markdown into HTML on the server (markdown2) and shows it at once through `ui.html`, sanitized in the browser like `ui.markdown`.
  - `progress.py` turns a job's progress into a percent and a status line. It reads the worker's phase lines through `common/phases.py`, and the number of pages read from gpt-researcher's `Scraped N pages` log lines.
  - `search.py` `/` (a section of `tabs.py`): the topic pill; «Подробно» opens a sheet with mode, sources, model, fact-check and «Уточнить тему». It takes `topic`, `mode`, `parent` query parameters (follow-up links). The fact-check follows the mode unless «Фокус» or the sheet sets it.
  - `job.py` `/job/{id}`: polls the queue every second; Дип digs, the percent, the last sources; error and interrupted states offer restart. A queued job has «Отменить» (back to the report it continues), and while it is held back the status counts the seconds. Once the text is being written, it shows under the percent paragraph by paragraph (`draft`, citations numbered as on the report page, without the list of sources at its end): Дип and the sources make room, the percent stays on top (`.writing`). A finished job's page redirects to its report; when the reader is already down the draft, a bar «Отчёт готов — открыть с этого места» opens it at the section being read (`?at=i`, app.js).
  - `report.py` `/report/{id}`:
    - the text only, with a sticky bar: «3 / 9 ▾» contents and «⋯» (files, sources with block buttons, questions, delete); above the text how long it reads (`common.report.reading_words()`) and, when the branch follows a route, «маршрут · k / N», which lists the steps (read ones open their reports, the others start in one tap);
    - citations become numbered superscripts (`cite()`), and a bordo «?» follows each sentence the fact-check could not confirm (`mark_doubtful()`); the list of sources the writer ends the text with is not shown (`common.report.without_references()`, 26.10.2026, the user's request: no wall of links before «Что исследовать дальше»): the sources are in «⋯ → Источники», the files keep the list;
    - selecting text offers «Сохранить», «Спросить», «Глубже» (`app.js` → `emitEvent('deeper_selection')`);
    - the fact-check block, «Что исследовать дальше», continuations and related reports at the end. Their anchors (`#factcheck`, `#next`) are `a.anchor` with a `scroll-margin-top` that clears the sticky bar;
    - «Что исследовать дальше»: the route's next step first (amber caption), then the topics, each with its kind, the reading time and the price of a report made as «Фокус» says (`kit.followup_sub()`). One tap starts it (`kit.start_followup()`: `service.submit()` with `parent`, `route_step` and `hold`) and opens its job page;
    - a scrim under the question pill (`.pill-scrim`): the text fades out below it instead of showing around it;
    - «⋯» → «Сократить до 1 500 слов» (for texts over `SHORT_FROM` = 1 800 words): the worker writes a short version once, and the page shows it at `?short=1`. Above it «Краткая версия · N слов · Полная →». The contents, the counter and the source numbers follow the short text; the fact-check's «?» marks and block stay with the full one.
  - `library.py` `/library` (a section): reports (running ones first) with their reading time, bookmarks, «Слежу» (empty until topic watching exists), search.
  - `focus.py` `/focus` (a section): presets and defaults, the blocklist, graffiti, the profile card.
  - `radar.py` `/radar` (a section): three tabs, «Новости», «Возможности» and «Мои» (`kit.text_tabs`, the choice in prefs `radar_tab`).
    - «Возможности»: the news part's list as its morning update left it (`GET /opps/board`: `items` with `story_id`, `status`, `opp_type`, `type_short`, `title_ru`, `title`, `meta`, `fit_reason`, `summary_ru`, `apply`, `details`, `new`; `new`, `later`, `updated`, `next`, `window_days`, `expected` with `name`, `opp_type`, `url`, `when`). New opportunities reach the user only here since 26.10.2026: due within three months (six for a big one), updated at 08:00. Above the list «обновлено сегодня в 08:00», a filter by type (`seg wrap`), «Новые · N» (this morning's), «Ждём · N» (`_awaited()`: big annual events whose registration usually opens by now and which the radar has not found, `expected` of the board; a tap opens the search screen with the event as a topic, the arrow its official page; the filter applies to them too), «Открытые · N», and under them how many lie beyond the window (`_later()`); an empty list says when the next update comes. The arrow opens where to apply. A row opens the opportunity's sheet (`_opp_sheet()`): the caption, what it is, why it fits, «Страница подачи» (a real link: a new tab opened from the server is a blocked pop-up on a phone), «В мои» / «Регистрация есть» above the facts (`details`, label and text as the news part writes them), then «Исследовать» (the search screen with it as a quick research topic), «Убрать из «Моих»», «Не моё».
    - «Мои»: the user's own (`GET /opps?status=tracked,applied`): «Регистрации ещё нет», «Регистрация есть», and those whose event is behind (`over`) under «Прошли» (the latest `PAST_SHOWN`). The circle before a row (`.mark`, bordo when on) switches tracked ↔ applied in one tap.
    - Every choice goes to the news part's `POST /opps/{id}/status`, the same statuses as the bot's buttons (a tracked one gets the bot's deadline reminders, an applied one no more); the open tab is then fetched and redrawn. `tabs.page()` fetches the board and «Мои» with the news.
    - «Новости»: the news part's stories of the last day and a half (`/feed`): the most important as the card with «Копнуть глубже» (the story as a research topic) and a link to its best source, the next as rows; below, the follow-up topics of recent reports not researched yet (as the card only while there is no news: one amber action), started in one tap like under a report. `tabs.page()` is async and fetches the news first, so the section has them at the first paint; showing it again re-fetches and redraws only on a change. When the news part does not answer, the section shows the topics only.
  - `login.py` `/login`.
- `core/bookmarks.py`: passages saved from reports, in `REPORTS_DIR/bookmarks.json`, keyed by the report's file name as well as its id.
- `core/library.py`: SQLite with an FTS5 table. Report files are the source of truth. Each row keeps `reading_words` (the words a reader reads), counted when the report is indexed; `sync()` counts it for rows indexed before it existed. `Entry.reading_min` turns it into minutes. `sync()` re-indexes `reports/<date>_*.json` (other JSON there, such as `read_stats.json` and `.qa.json` files, is skipped), so the DB can be deleted and rebuilt. Search uses per-word prefix matching so that Russian word forms match.
- Research threads: a report started from another one stores `parent` = the parent's file name, not its library id, because ids change when the DB is rebuilt. The library's `parent` column is added by `ALTER TABLE` on older DBs; `children()`, `by_name()` and `related()` (OR prefix match on titles) feed the report page.
- `core/short.py`: a report's short version as `<name>.short.md`, which the worker writes once (`/shorten`). `service.shorten()` holds a lock per report, so a second tap waits for the first instead of paying twice.
- `core/qa.py`: Q&A history next to each report as `<name>.qa.json`. When the report lacks the answer, the worker's model ends with an «ИССЛЕДОВАТЬ: тема» line, which is cut from the answer and kept as `research`; both UIs offer to run it.
- `core/blocklist.py`: `reports/blocked_domains.txt` (on the Railway volume), managed from «Фокус» → «Не брать», the report's sources sheet and the bot's `/block`, `/unblock`, `/blocked`; handed to each research with its job.

## Run and check it alone

```bash
uv run python -m my_browser.core     # core alone: web UI + API at 127.0.0.1:8501 (PORT)
uv run ../../app.py                  # core with the worker and the bot in one process, as on Railway
```

- Alone, core serves the pages and the API.
  - Questions, follow-ups and clarification go to the worker: at `WORKER_URL`, or in-process when it is unset (`uv run` installs the worker's group too).
  - Jobs wait in the queue until a worker's job loop takes them: run `python -m my_browser.worker` with `CORE_URL` pointing here, or use the all-in-one app.
- **Screens and flows.** Run the all-in-one app with the mock LLM, started as the `check-project` skill does: a throwaway `REPORTS_DIR`, `PORT=86xx PUBLIC=0`. Without `PUBLIC=0`, any `PORT` makes the app public and sends every page to the login.
- **Real reports.** A throwaway `REPORTS_DIR` starts with an empty library. To see real reports, copy a few `reports/<date>_*.{md,json}` into it; the library indexes them on start.
- **Phone first.** Look at every screen change at 360 and 390 px wide first: playwright with the installed Chrome (`channel="chrome"`, headless). The user mostly opens DEEper on the phone, installed from Safari.
- **The whole project.** Before committing, run the `check-project` skill: a research through the queue, the worker and search, then the main pages.

## What others rely on

**Worker** (`worker/runner.py`, over HTTP with `call_core`) uses the queue endpoints:
- `POST /api/worker/hello {worker}`: every running job of another worker id becomes interrupted.
- `POST /api/worker/claim {worker}`: 204 when nothing is queued (or only held-back jobs), otherwise the job plus `blocked` (the blocklist), `branch`, `route` and `profile`. The worker reads `id`, `query`, `mode`, `sources`, `writer`, `parent`, `factcheck`, `blocked`, `branch`, `route`, `route_step`, `profile`.
- `POST /api/worker/jobs/{id}/progress {worker, phase, queries, urls, log, cost_rub, draft}`: also the heartbeat (`HEARTBEAT_TIMEOUT` is 5 min, the worker sends at least every 30 s). It is ignored unless the job is running under that worker. `urls` are `[url, "3/3"]` pairs; `phase` is one of the lines in `common/phases.py`.
- `POST /api/worker/jobs/{id}/result {worker, files}`: `files` maps `.md`, `.json` and, when rendering worked, `.docx` and `.pdf` to base64. Accepted even after the job was given up as interrupted. The worker retries six times over about two minutes; a retry after a lost response would store the report twice, since nothing deduplicates by job id.
- `POST /api/worker/jobs/{id}/error {worker, error}`.

**Bot** (`bot/telegram.py`) uses the rest of `/api` and reads these fields; a renamed field breaks a handler at run time, not at import:
- job: `id`, `active`, `status`, `phase`, `elapsed`, `error`, `report_id`, `query`, `mode`, `sources`, `factcheck`, `origin_ref`, `not_before`;
- `POST /api/jobs` takes `factcheck` as true, false or absent; absent means the mode decides (`FACTCHECK_MODES`), resolved in `service.submit()` and stored as a bool. `mode` defaults to `DEFAULT_MODE`; `route_step` and `hold` (seconds) are for a follow-up started by one tap. `POST /api/jobs/{id}/cancel` → `cancelled` (false once it has started);
- `GET /api/reports/{id}` (`service.report_view`): `entry` (`id`, `query`, `words`, `sources_count`, `duration_sec`, `name`, `reading_min`), `meta` (the report JSON as stored), `markdown`, `qa`, `route` (`service.route()` with the root's id, or null);
- `GET /api/reports` (plus `snippet` when searching with `q`), `GET /api/reports/{id}/files/{pdf|docx|md}` (404 for a missing file; the bot sends what exists), `DELETE /api/reports/{id}` → `deleted`;
- `POST /api/reports/{id}/shorten` → `{markdown, words, cost_rub}` (`cost_rub` is null when the short version already existed; the bot does not use it yet);
- `POST /api/reports/{id}/ask` → the stored Q&A item (`a`, `cost_rub`, `research`); `/api/clarify` → `questions`, `cost_rub`; `/api/refine` → `topic`, `cost_rub`; blocklist → `domains`, `domain`, `removed`;
- `GET /api/jobs?status=interrupted&origin=telegram` lists only jobs the bot has not reported yet; it then calls `POST /api/jobs/{id}/notified`.
- The bot addresses follow-up topics, route steps and Q&A research topics by position (`f:<report>:<i>`, `rt:<report>:<step>`, `r:<report>:<i>`; Telegram limits callback data to 64 bytes). Keep `meta.followups` and `meta.route` in their order and `.qa.json` append-only.

**In-process users**: `research.py` and `eval/run.py` call `Library().store(files)` directly, without the API or the queue, and `research.py` also calls `blocklist.blocked_domains()`. `app.py` imports `core.web` and registers the job loop and the bot on `web.app` (NiceGUI's app).

## What core relies on

- **The worker's API**, through `common.clients.call("worker", ...)` in `service.py`; request and response shapes are in `worker/CLAUDE.md`. With `WORKER_URL` unset it runs inside core's process and imports `my_browser.worker.api`, and gpt-researcher with it. So core installed alone (`--group core`) needs `WORKER_URL`.
- **The worker's progress**: the phase lines and their patterns in `common/phases.py`, which `ui/progress.py` reads; a line it cannot read leaves the percent where it was. The pages read is counted from gpt-researcher's own `Scraped N pages` log line in the job's log tail.
- **The report files written by the worker's `pipeline.render()`**:
  - `Library.add` requires `query`, `mode`, `created`, `words`, `duration_sec` and `sources` (a list); `parent` is optional. `store_result` reads `cost.rub`.
  - The pages read `writer`, `cost`, `followups`, `followups_cost_rub`, `sources` and `factcheck` (`checked`; `doubtful` items with `raw`, `verdict`, `note`). Reports already on the volume predate some fields, so read newer ones with `.get()`.
  - The Markdown ends with «Проверка фактов» (when checked), then «Что исследовать дальше»; `common.report.body()` cuts both off for the page, and `appended()` gives the fact-check text for its block.
  - `mark_doubtful()` finds each doubtful sentence by its `raw` text (whitespace-tolerant) to put the «?» after it, so a worker change to how claims are cut out of the text shows up here as missing marks.
- **The news part's `/profile`** (`service.claim()`, for the follow-ups «Для меня»): `text` and `custom`; a failure only leaves the profile out.
- **The news part's `/feed`, `/opps/board`, `/opps` and `/opps/{id}/status`** (`ui/radar.py`), through `common.clients.call("news", ...)`, in-process unless `NEWS_URL` is set; the fields are in `news/CLAUDE.md`. An opportunity's caption (`meta`), its sheet's facts (`details`) and the filter's short type names (`type_short`) are written there, so the screen formats nothing.
- **`common`**: `settings`, `auth`, `report`, `text`, `clients`, `phases`. Core imports no module of the worker, search, news or bot.

## State core owns

All of it is in `REPORTS_DIR` (the Railway volume), next to search's `read_stats.json`:
- `library.sqlite`: tables `reports` and `reports_fts` (`library.py`) and `jobs` (`jobs.py`), each created on first use; a column added to `jobs` later goes into `ADDED_COLUMNS`, so older databases on the volume get it. `sync()` rebuilds the report tables from the files; the queue cannot be rebuilt, so deleting the database also drops queued and finished jobs.
- `<date>_<time>_<slug>.{md,json,docx,pdf}`, `<name>.qa.json` and `<name>.short.md`; `Library.delete()` removes all of them. `sync()` indexes only `<date>_*.json` files that are not `.qa.json`, so other JSON in the directory is left alone.
- `blocked_domains.txt` (eval reads it straight from `reports/`), `.service_token` (when `SERVICE_TOKEN` is unset, every part with the same `REPORTS_DIR` reads it), `.storage_secret` and `.nicegui/` (logins, and each browser's «Фокус» settings in `app.storage.user["prefs"]`).
- `bookmarks.json` (`bookmarks.py`): passages saved from reports. Each keeps the report's file name besides its id, since ids change when the library is rebuilt; deleting a report leaves its bookmarks, shown as «отчёт удалён».

## Constraints

- One core process per `REPORTS_DIR`: the queue's write lock is per process (Railway runs one replica).
- One worker: `hello()` interrupts the running jobs of every other worker id, so a second worker starting would cut off the first one's research. Several workers would need a hello that gives up only the restarted worker's own jobs, leaving the rest to the heartbeat.
- `web.py` sets `NICEGUI_STORAGE_PATH` before importing NiceGUI, which reads the variable at import. Import NiceGUI only through `core.web` (as `app.py` does), and the screens in `core/ui/` likewise only from `web.py`; otherwise logins live outside the volume and every redeploy logs everyone out.
- **Motion and the phone** (`static/app.css`, `static/app.js`):
  - The tab bar's sections switch in the page (`tabs.py`) and enter with a short rise (`screen-in`). Other screens are pages of their own: the old one fades into the new by a cross-document view transition (Safari 18.2+, Chrome 126+), with the tab bar in place (`view-transition-name: tabbar`, which must be unique on a page). Where no view transition runs, the page enters with the same rise.
    - No transition: an older browser, a navigation started by a script (`ui.navigate.to`), a page slower than 4 s.
    - Telling the two apart: an inline script in web.py's head marks a page a view transition brought (`html.vt`, from `pagereveal`), so it gets no entrance of its own.
    - Transform only `.screen` and the elements inside it, never an ancestor of a fixed element (the tab bar, the pills, `.home-glow`): a transformed ancestor becomes the containing block of `position: fixed`.
  - A section hidden when the page loads lays out nothing. Quasar's autogrow textarea measured 0 px there and hid the search pill's field, so it has `min-height: 1.4em !important`. Anything else that measures itself on mount needs the same care.
  - A tap answers at once: `:active` dims rows, tabs and icon buttons (iOS applies `:active` only because app.js listens to `touchstart`). While the next screen loads, `html.nav-pending` draws an amber line at the top: on app links, and on server navigations where the browser has the Navigation API; not on downloads, new tabs or `history.replaceState`.
  - Safari stops every animation of a page once it starts leaving it, so feedback for a navigation must work without one: the line is drawn at a third of the width at once and grows only where animations go on.
  - Animate `transform` and `opacity` only: the job page's bar is a `scaleX`, the percent counts up in app.js (written into the same text node Vue keeps updating).
  - A moving part of a mascot carries its own piece of the sticker outline: the shovel's is `.dip-tool-sticker`, in both of the sticker's layers, animated like `.dip-tool`. In one static outline it stayed behind while the shovel swung, a cream blob over the body.
  - The report's «3 / 9» measures the headings' positions once (and on resize) and compares numbers on scroll; it lands right after a jump across sections.
- **iOS 26 home-screen app** (WebKit bug 301108): with `viewport-fit=cover` and the translucent status bar, a page shorter than the screen gets a viewport shorter by the status bar's height (about 59 pt). The tab bar then floated that high above the bottom on short screens (the search screen) and jumped when switching to a long one. `@media (display-mode: standalone) { html { min-height: min(100lvh, calc(100% + env(safe-area-inset-top))) } }` in app.css gets the full viewport; it cannot be reproduced in Chrome or Playwright's WebKit, only on an iPhone. Shifting the bar by `screen.height - innerHeight` in JS made it worse in another project.
- **A row that does not wrap** (`.continue`: «Продолжить: <тема> →», «продолжение отчёта #N: <тема>») gives way through its `.continue-topic` with an ellipsis. A tab section is a flex item of NiceGUI's column, so `.tab-screen.on` keeps `width: 100%; min-width: 0`: without it, a long latest topic widened the whole section past the phone's width (541 px on a 360 px screen, 26.10.2026).
- **Never `ui.markdown` on the screens**: it renders an empty block, fetches its code-highlighting stylesheet, and only then the text. On a phone everything below a report showed up at the top for a moment, overlapping, and jumped down (layout shift 0.67 on a slow profile, 0 with `kit.markdown`).
- **NiceGUI gives every element an `id`** (`c123`): never select by `[id]`. `.end-block a[id]` once moved the end blocks' link rows 80 px up, over the labels and the mascot.
- **Closed sheets stay in the page** until the user leaves it: deleting one re-renders its siblings, and a sheet opened from it («⋯» → «Источники») vanished.
- The graffiti fonts (Permanent Marker, Amatic SC) are `font-display: block`: in a fallback script font a marker word, wider and taller, overlapped its neighbours until the font arrived.
- Styles live in `static/app.css`. Quasar puts its own `!important` color classes (`bg-primary`, `text-white`) in a CSS cascade layer, and an `!important` declaration inside a layer beats one outside it, so app.css cannot override a component's color: give it through props (`color=accent text-color=night`), as `ui/kit.primary()` does. Avoid class names Quasar already uses (`.row`, `.column`, `.col`…): its `.row` wraps flex children.
- `PORT` also switches on public mode: bind to 0.0.0.0 and require `APP_PASSWORD` (Railway sets `PORT`). `PUBLIC=0` keeps a local copy on another port private. The worker and the bot find core at `CORE_URL`, by default `http://127.0.0.1:$PORT`.
- `OPEN_PATHS` must keep `/api/` open to the login middleware: the bot and the worker carry the service token, not a login.
- The API does not validate `mode`, `sources` and `writer`. An unknown key is queued and then fails in the worker (`SOURCES[...]`, `WRITERS[...]`), and `MODES[job["mode"]]` in the pages and `MODE_BUTTONS[...]` in the bot raise on it.
- The pages call `service` and `jobs` directly, the bot goes through the API: an operation both need belongs in `service.py`, with a thin route in `api.py`.
- No module here may take a stdlib module's name (`queue`, `json`, `logging`…): started from this folder, `python -m` puts the folder first on `sys.path`, and the stdlib's module would be shadowed.

## When you change…

| Change | Also update |
|---|---|
| a job field or status | the bot (`Status.follow`, `finish`, `notify_interrupted`), `ui/job.py` and `ui/progress.py`, the running rows of `ui/library.py`, `worker/runner.py` if it sends or reads the field |
| what a continuation is told (`branch`, `route`, `profile` in `claim()`) | `worker/prompts.reader_text()` and `worker/suggest.followups()` read them |
| `HOLD_SEC` | the bot's `HOLD_SEC` and its «Начну через N с» |
| an `/api` route or response field | the bot; for `/api/worker/*` also `worker/runner.py` |
| how the job page reads a phase | the phase lines live in `common/phases.py`, shared with the worker; the bot shows them as they are |
| what the pages read from the report JSON | it is written by `worker/pipeline.render()`; old reports lack newer fields |
| a mode, source or writer key | `common/settings.py` (also `FACTCHECK_MODES`, `DEFAULT_MODE`); `ui/kit.py` (`MODES`, `MODE_NAMES`, `MODE_HINTS`, `READ_HINTS`, `PRICE_GUESS`, `SOURCE_LABELS`, `WRITER_LABELS`, `WRITER_HINTS`, `WRITER_OF_MODEL`, `DEFAULT_PREFS`; a new default mode needs `PREFS_VERSION` raised, or stored choices keep the old one) and the presets in `ui/focus.py`; the bot (`MODE_BUTTONS`, `SOURCE_BUTTONS`, `HELP`) |
| what the report page shows of the fact-check | it comes from `worker/factcheck.summary()` and `section()`; the bot's `_factcheck_line()` reads the same counts |
| the time estimates of the modes | `ui/kit.MODE_HINTS` and the bot's `HELP` |
| report file naming (`Library.store`, `common.text.slugify`) | `REPORT_NAME` in `library.py`; parents are stored by file name |
| what «Радар» shows of a story | the fields come from `news/delivery.feed()` |
| what «Радар» shows of an opportunity, or a status it sets | the fields come from `news/opps_delivery.board()` and `dressed()` (`meta`, `type_short`, `apply`, `details`, `over`, `new`) and `news/opps.listing()`; the statuses are the bot's buttons' (`news/opps_delivery.STATUSES`), so a new one starts in the news part |
| an icon the app did not show before, or a NiceGUI upgrade | `uv run dev/icons.py` cuts `static/fonts/icons.woff2` anew (the `check-project` skill runs `dev/icons.py --check`); a name built at run time escapes its scan, so write icon names as literals |

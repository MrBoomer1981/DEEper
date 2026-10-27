# CLAUDE.md: bot

The bot is DEEper in Telegram: it starts researches, follows them and sends the reports, all through core's API. This file covers what it does, how to run and check it alone, and what it expects from core, from Telegram and from the process it runs in. The repository's rules for working in one part are in the root CLAUDE.md. Paths below are relative to `my_browser/` unless they are repo-root files.

## What the bot does

`bot/telegram.py` (aiogram 3) uses core's API, and the news part's for `/news`; report files are downloaded from core and sent as `BufferedInputFile`.
- Access is limited to `TELEGRAM_ALLOWED_USERS` through an outer middleware; anyone else is only told their ID. `TELEGRAM_TOKEN` (the DEEper v1 name) works as a fallback for `TELEGRAM_BOT_TOKEN`.
- A plain-text message is treated as a topic and answered with mode and source buttons («⚡ Обзор», the 5–7 minute report, comes first). A research is a job in core; a status message follows it every few seconds and shows the worker's phase line as it is.
- After a report: the files with a caption (reading time, words, sources, time, cost), its «главное» (the overview's `## Главное`, else a longer report's conclusion: `_summary()`, without the citations in brackets), the fact-check line, then «Что исследовать дальше»: the route's next step first (`rt:`), then the topics by number (`f:`). One tap starts an overview that continues the report at once (`start_followup()`): it is queued with `hold` (`HOLD_SEC`, 5 s) and `route_step` for a route's step, and its status message has «✖️ Отменить» (`cx:`) while it waits.
- Replying to any message that contains `#N`, or using `/ask N`, answers from report N's text.
- Callback data prefixes:
  - `m:`/`s:` mode and sources;
  - `k:` the fact-check toggle (as the mode has it → yes → no);
  - `c:`/`a:` clarification;
  - `f:<report id>:<index>` a follow-up topic, started at once; `rt:<report id>:<step>` the route's next step, started at once;
  - `cx:<job id>` cancel a job that has not started yet (`POST /api/jobs/{id}/cancel`; a cancelled job is gone, so `Status.follow()` takes the 404 for «cancelled»);
  - `r:<report id>:<qa index>` a question's research topic;
  - `x:<job id>` restart a failed or interrupted job;
  - `nr:<story id>`, `nf:<story id>:<±1>` a news story's research and feedback;
  - `nt:<topic>` a news topic switched on or off;
  - `nd:<story id>` a dropped news story brought back;
  - `op:`, `ot:`, `oy:`: the opportunities' buttons of messages sent before 27.10.2026; `on_moved()` answers that they moved to OnePath.

  Topics are read back through the API, because Telegram limits callback data to 64 bytes. `pending` holds the offered topics, keyed by the message that offered them.
- Every minute it looks for Telegram jobs interrupted by a restart and edits their status message to offer a restart.
- `/news` shows the news part's status (`news_text()`): sources and their health, posts and stories of the last day, LLM spend, the schedule.
- `deliver_news()` takes the news part's urgent stories and digests every `NEWS_SEC` (30 s) and sends them to every `TELEGRAM_ALLOWED_USERS` id, text as the news part wrote it (HTML, split by `_chunks()`), buttons on the last part. A message Telegram refuses is logged and given up, so it is not resent every round.
- `nr:<story id>` offers a news story as a research topic (`_offer()`, as follow-up topics); `nf:<story id>:<±1>` stores 👍/👎 in the news part.
- News settings: `/time`, `/quiet`, `/urgent` (each with `off`), `/pause`, `/resume` change the news part's settings (`_news_settings()`; a refused value comes back with the news part's message, in Russian); `/topics` toggles topics with buttons (`nt:<topic>`); `/sources` lists the sources with their health, `/add` checks and adds a feed or a channel, `/remove` switches one off by number or name.
- `/dropped`: what the news part dropped in the last day, grouped (minor, not about AI) with the reasons; «↩️ N» (`nd:<story id>`) brings a story back: it comes as a message and counts as 👍.
- Opportunities (hackathons, schools, internships, grants) moved to their own app, OnePath, on 27.10.2026: it has no bot, and its deadlines are in the app («Скоро дедлайн»). `/opps`, `/deadlines`, `/opptypes` and `/add opps …` answer that they moved (`MOVED`), and so do the old messages' buttons (`on_moved()`).
- `/profile` shows the profile the research follow-ups «Для меня» know (`profile` of `GET /settings`, empty until set); `/profile <text>` replaces it through `_news_settings()` (the news part refuses below 20 or above 3 000 characters). The user's real profile lives only there, never in the repository.

## Run and check it alone

```bash
uv run python -m my_browser.bot       # needs core at CORE_URL and a bot token; exits with a hint without a token
```

- **Never with the production token.** Railway polls with it, and two pollers break the bot for the user. Local runs keep `TELEGRAM_BOT_TOKEN` and `TELEGRAM_TOKEN` empty.
- **A live try** needs a test bot. Ask the user to create one at @BotFather and give its token.
  - Pass that token as `TELEGRAM_BOT_TOKEN` for that run only, with the user's id in `TELEGRAM_ALLOWED_USERS`.
  - Run it against a local core: the all-in-one app with the mock LLM, as the `check-project` skill starts it, with `CORE_URL` pointing at it.
- **Without a token**, check the import with only the bot's group, in a throwaway environment: `UV_PROJECT_ENVIRONMENT=<scratch>/venv-bot uv run --no-default-groups --group bot python -c "import my_browser.bot.telegram"`. Check the handlers' logic by reading them against the fields core's API returns (`core/CLAUDE.md`).
- **The whole project.** Before committing, run the `check-project` skill.

## What the bot relies on

- **Core's API**: `call("core", ...)` at `CORE_URL` with the service token.
  - `core/CLAUDE.md` lists the fields the bot reads and the order core keeps for follow-ups and Q&A.
  - The bot imports no module of core, the worker, search or news, so it can run alone with the `bot` group (aiogram, httpx, python-dotenv). Keep it that way.
- **The news part's API**: `/status` for `/news`, `/deliveries` and `/deliveries/{id}/sent` for delivery, `/stories/{id}` and `/feedback` for the buttons, `/settings` and `/sources` for the settings commands; `news/CLAUDE.md` lists the fields. With `NEWS_URL` unset the call runs in-process and imports `my_browser.news.api`, so a bot installed alone (`--group bot`) needs `NEWS_URL`; without it `/news` answers «Новости сейчас недоступны».
- **`common`**: `clients.call`, `report.KINDS` and `ANY_ANSWER`, `settings.DEFAULT_SOURCES` and `FACTCHECK_MODES`, `text.rub_text` and `plural`.
  - It must not import `common.costs`, `common.gptr` or `common.auth`: they pull in gpt-researcher and fastapi.
- **Its own copies of what lives elsewhere**:
  - `MODE_BUTTONS` and `SOURCE_BUTTONS`, whose keys come from `common/settings.py`;
  - the time estimates in `HELP`, which are also in `core/ui/kit.MODE_HINTS`;
  - the summary pattern in `_summary()`: the overview's level-2 «Главное» (`Key points` in English), else a level-2 «Заключение», «Выводы» or «Conclusion» heading, as the worker writes them;
  - `GET /api/reports/{id}`'s `route` (`steps`, `next`) for the route's button, `entry.reading_min` for the reading time; `POST /api/jobs` with `route_step` and `hold`, and `POST /api/jobs/{id}/cancel` → `cancelled`;
  - `_factcheck_line()`, which reads `checked`, `relinked`, `rewritten` and `doubtful` of the report's `factcheck`, like the web's chip.
  - The fact-check toggle sends `factcheck` true, false or nothing (as the mode has it); the status header guesses the mode's default from `FACTCHECK_MODES`, core decides.
  - The writer is never sent, so core's default (`DEFAULT_WRITER`) applies.
- **Telegram**:
  - `callback_data` holds at most 64 bytes, so it never carries a topic:
    - `f:<report>:<i>`, `rt:<report>:<step>` and `r:<report>:<i>` read the topic back from core by position;
    - `cx:<job>` cancels a job;
    - `m:`, `s:`, `k:` (fact-check toggle), `c:` and `a:` refer to `pending` by the key of the message that offered the topic;
    - `x:<job>` restarts a job.
  - Messages use HTML (`ParseMode.HTML`): escape everything from reports or users with `html.escape`. Text longer than `TG_LIMIT` goes through `_chunks()`.
  - A media group takes 2–10 files, so a single file goes as one document.
  - One poller per token. Railway runs one replica, and local runs blank `TELEGRAM_BOT_TOKEN` and `TELEGRAM_TOKEN`; otherwise both pollers fail with a conflict.
  - The `AllowedUsers` outer middleware checks access on messages and callback queries. A new update type (documents, inline queries) needs it too.
- **The process**:
  - In the all-in-one app, `app.py` starts `start(handle_signals=False)` as a task on NiceGUI's event loop. A blocking call in a handler freezes the web UI too, so use async I/O only.
  - Alone, it runs as `python -m my_browser.bot`.

## State and its limits

- `pending` (offered topics, clarification answers) and every `Status.follow()` task live in memory and end with the process.
- After a restart, only interrupted jobs get their message back (`notify_interrupted()` uses `origin_ref`).
  - A Telegram job that was queued, or with separate processes still running, is finished by the worker, but nobody sends the report to the chat; it only shows up in /history.
  - Following the active `origin=telegram` jobs on start would close that gap.
- A failed core request inside `follow()` (core restarting, with separate processes) ends that task silently.

## When you change…

| Change | Also update |
|---|---|
| a command | `set_my_commands()` in `start()`, and `HELP` |
| a callback prefix | keep the data within 64 bytes; the prefix list above |
| the fields read from core | check them in `core/service.py` and `core/jobs.py`, not only here |
| a mode or source | the table in `core/CLAUDE.md` |
| what `/news` shows | the fields of the news part's `/status` (`news/CLAUDE.md`) |
| how news messages are sent | the news part writes text and buttons (`news/delivery.py`); the bot sends them as they are |

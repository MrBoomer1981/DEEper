# CLAUDE.md: bot

The bot is DEEper in Telegram: it starts researches, follows them and sends the reports, all through core's API. This file covers what it does, how to run and check it alone, and what it expects from core, from Telegram and from the process it runs in. The repository's rules for working in one part are in the root CLAUDE.md. Paths below are relative to `my_browser/` unless they are repo-root files.

## What the bot does

`bot/telegram.py` (aiogram 3) uses core's API only; report files are downloaded from core and sent as `BufferedInputFile`.
- Access is limited to `TELEGRAM_ALLOWED_USERS` through an outer middleware; anyone else is only told their ID. `TELEGRAM_TOKEN` (the DEEper v1 name) works as a fallback for `TELEGRAM_BOT_TOKEN`.
- A plain-text message is treated as a topic and answered with mode and source buttons. A research is a job in core; a status message follows it every few seconds and shows the worker's phase line as it is.
- Replying to any message that contains `#N`, or using `/ask N`, answers from report N's text.
- Callback data prefixes:
  - `m:`/`s:` mode and sources;
  - `k:` the fact-check toggle (as the mode has it → yes → no);
  - `c:`/`a:` clarification;
  - `f:<report id>:<index>` a follow-up topic;
  - `r:<report id>:<qa index>` a question's research topic;
  - `x:<job id>` restart a failed or interrupted job.

  Topics are read back through the API, because Telegram limits callback data to 64 bytes. `pending` holds the offered topics, keyed by the message that offered them.
- Every minute it looks for Telegram jobs interrupted by a restart and edits their status message to offer a restart.

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

- **Core's API only**: `call("core", ...)` at `CORE_URL` with the service token.
  - `core/CLAUDE.md` lists the fields the bot reads and the order core keeps for follow-ups and Q&A.
  - The bot imports no module of core, the worker or search, so it can run alone with the `bot` group (aiogram, httpx, python-dotenv). Keep it that way.
- **`common`**: `clients.call`, `report.KINDS` and `ANY_ANSWER`, `settings.DEFAULT_SOURCES` and `FACTCHECK_MODES`, `text.rub_text`.
  - It must not import `common.costs`, `common.gptr` or `common.auth`: they pull in gpt-researcher and fastapi.
- **Its own copies of what lives elsewhere**:
  - `MODE_BUTTONS` and `SOURCE_BUTTONS`, whose keys come from `common/settings.py`;
  - the time estimates in `HELP`, which are also in `core/ui/kit.MODE_HINTS`;
  - the conclusion pattern in `_conclusion()`: a level-2 «Заключение», «Выводы» or «Conclusion» heading, as the worker writes it;
  - `_factcheck_line()`, which reads `checked`, `relinked`, `rewritten` and `doubtful` of the report's `factcheck`, like the web's chip.
  - The fact-check toggle sends `factcheck` true, false or nothing (as the mode has it); the status header guesses the mode's default from `FACTCHECK_MODES`, core decides.
  - The writer is never sent, so core's default (`DEFAULT_WRITER`) applies.
- **Telegram**:
  - `callback_data` holds at most 64 bytes, so it never carries a topic:
    - `f:<report>:<i>` and `r:<report>:<i>` read the topic back from core by position;
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

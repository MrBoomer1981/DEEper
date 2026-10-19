# CLAUDE.md: bot

Guidance for changing `my_browser/bot/`: what the bot expects from core, from Telegram and from the process it runs in. What the bot does is in the root CLAUDE.md; paths below are relative to `my_browser/` unless they are repo-root files.

## What the bot relies on

- **Core's API only**: `call("core", ...)` at `CORE_URL` with the service token.
  - `core/CLAUDE.md` lists the fields the bot reads and the order core keeps for follow-ups and Q&A.
  - The bot imports no module of core, the worker or search, so it can run alone with the `bot` group (aiogram, httpx, python-dotenv). Keep it that way.
- **`common`**: `clients.call`, `report.KINDS` and `ANY_ANSWER`, `settings.DEFAULT_SOURCES`, `text.rub_text`.
  - It must not import `common.costs`, `common.gptr` or `common.auth`: they pull in gpt-researcher and fastapi.
- **Its own copies of what lives elsewhere**:
  - `MODE_BUTTONS` and `SOURCE_BUTTONS`, whose keys come from `common/settings.py`;
  - the time estimates in `HELP`, which are also in `core/web.MODE_HINTS`;
  - the conclusion pattern in `_conclusion()`: a level-2 «Заключение», «Выводы» or «Conclusion» heading, as the worker writes it.
  - The writer is never sent, so core's default (`DEFAULT_WRITER`) applies.
- **Telegram**:
  - `callback_data` holds at most 64 bytes, so it never carries a topic:
    - `f:<report>:<i>` and `r:<report>:<i>` read the topic back from core by position;
    - `m:`, `s:`, `c:` and `a:` refer to `pending` by the key of the message that offered the topic;
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
| a callback prefix | keep the data within 64 bytes; the prefix list in the root CLAUDE.md |
| the fields read from core | check them in `core/service.py` and `core/queue.py`, not only here |
| a mode or source | the table in `core/CLAUDE.md` |

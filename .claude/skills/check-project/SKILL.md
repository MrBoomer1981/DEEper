---
name: check-project
description: Whole-project check of DEEper before a commit or push - every module imports, then one free end-to-end research through the all-in-one app with the mock LLM. Use after changing any part (core, worker, search, bot, common, eval), before committing, and when asked to check that nothing broke («проверь, что ничего не сломалось»).
---

# Whole-project check

A change in one part can break another: at import (a renamed function), or only at run time (an API field, a phase text, a field of the report JSON). Run this before every commit; a push to `main` deploys to Railway. It costs nothing: the LLM is `dev/mock_llm.py`, the reports go to a throwaway directory, and the app gets its own port, so it does not meet another session's run or the user's own local app.

Work from any folder of the repository. Shell variables do not survive between commands, so note the paths and ports you pick.

## 1. Every module compiles and imports

```bash
R=$(git rev-parse --show-toplevel)
uv run python -m compileall -q "$R/my_browser" "$R/app.py" "$R/research.py" "$R/eval" "$R/dev" &&
REPORTS_DIR=$(mktemp -d) uv run python -c "import importlib, pkgutil, my_browser; [importlib.import_module(m.name) for m in pkgutil.walk_packages(my_browser.__path__, 'my_browser.') if not m.name.endswith('__main__')]; print('imports ok')"
```

## 2. One research end to end, free

Pick a throwaway directory `D` in your scratchpad and two free ports: the app's (from 8601 up) and the mock's (from 8765 up). Check a port with `lsof -ti :<port>`; another session may be running this check too. Start the mock and the app in the background:

```bash
R=$(git rev-parse --show-toplevel)
uv run "$R/dev/mock_llm.py" --port <mock> > "$D/mock.log" 2>&1 &
FAST_LLM=openai:mock SMART_LLM=openai:mock STRATEGIC_LLM=openai:mock OPENAI_API_KEY=mock \
OPENAI_BASE_URL=http://127.0.0.1:<mock>/v1 REPORTS_DIR="$D/reports" PORT=<port> PUBLIC=0 \
TELEGRAM_BOT_TOKEN= TELEGRAM_TOKEN= uv run "$R/app.py" > "$D/app.log" 2>&1 &
```

- `PUBLIC=0` keeps it on 127.0.0.1 without a password, whatever the port.
- Empty Telegram tokens: a second bot poller would break the bot on Railway.
- The mock's address goes through environment variables, which beat `config.json` and the writer toggle.

When `curl -s localhost:<port>/login` answers, submit a research through core's API, as the bot does:

```bash
H="Authorization: Bearer $(cat "$D/reports/.service_token")"
curl -s -X POST localhost:<port>/api/jobs -H "$H" -H 'Content-Type: application/json' \
  -d '{"query": "натрий-ионные аккумуляторы", "mode": "quick", "sources": "all", "origin": "web", "factcheck": true}'
curl -s localhost:<port>/api/jobs/<job id> -H "$H"    # every 10-15 s until status is done or error
curl -s localhost:<port>/api/reports/<report_id> -H "$H"
for p in / /library /report/<report_id> /job/<job id>; do curl -sL -o /dev/null -w "$p %{http_code}\n" "localhost:<port>$p"; done
```

- `sources: all` needs the local SearXNG. If the job fails at «Проверяю DeepSeek и поиск» saying SearXNG does not answer, start it with `docker compose up -d` (it finds the compose file from any folder), or use `academic`.
- A quick research takes 1–2 minutes: the search engines and the pages are real, only the LLM is fake.
- `"mode": "detailed"` (2–4 minutes) also goes through the subtopics and the sections. Use it after a change to `worker/detailed.py`.
- With the mock, everything after the research takes a second, and progress reaches core every 3 s. So the job never shows the phases of the sections, the fact-check or the files. After changing a phase (`common/phases.py`, `core/ui/progress.py`), feed the texts the worker writes through `percent()` and `status()` of `core/ui/progress.py` instead.

The check passes when:
- the job ends `done`;
- the report's `meta` has `sources`, `followups` and a `factcheck` with `checked` > 0. The mock usually gets a figure corrected (12,5 → 11,9), so expect `rewritten` ≥ 1;
- every page answers 200 (`-L`: a finished job's page redirects to its report);
- `grep -c Traceback "$D/app.log"` is 0.

If a step fails, read the end of `app.log` and of the job (`phase`, `error`, `log`).

## 3. Clean up

Stop what you started, by port: uv wraps Python, so killing the uv process leaves the server running.

```bash
lsof -t -i :<port> -i :<mock> | xargs kill
```

## Also, when the change calls for it

- **UI**: screenshots at 360 and 390 px wide. Use `uv run --with playwright`, driving the installed Chrome (`channel="chrome"`, headless). The user mostly uses the phone.
- **Search or writing quality**: `uv run eval/run.py`, against the last run made the same way. It costs real money: about 10 ₽ for the quick set, about 12 ₽ for a detailed Pro topic. Ask the user first.
- **The bot**: only with a separate test bot's token. The production token conflicts with the poller on Railway.
- **Dependencies or the Dockerfile**: `docker build -t deeper-check .` (about 6 minutes).

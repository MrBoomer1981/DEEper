# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal deep-research engine that writes long reports: the `gpt-researcher` pip library for orchestration, a local SearXNG (or Tavily on Railway) plus arXiv/OpenAlex for search, and DeepSeek as the LLM (Flash for search and planning, V4 Pro writes the text by default). The user writes in Russian; reports default to Russian. The roadmap is `plan.md` (Russian, checkboxes).

## Commands

```bash
docker compose up -d                       # local SearXNG on 127.0.0.1:8080 (needs SEARXNG_SECRET in .env)
uv run app.py                              # everything in one process: web UI + API at 127.0.0.1:8501, job loop, bot if a token is set
uv run python -m my_browser.core           # or each part on its own: core (web UI + API, PORT=8501),
uv run python -m my_browser.worker         #   worker (API on WORKER_PORT=8502 + job loop), search (SEARCH_PORT=8503), bot;
uv run python -m my_browser.search         #   set WORKER_URL / SEARCH_URL / CORE_URL so they find each other
uv run python -m my_browser.bot
uv run research.py "тема"                  # one research without any server -> reports/ (detailed by default)
uv run research.py "тема" --mode deep --sources all --words 2500
uv run research.py "тема" --mode quick --factcheck   # fact-check on (default: detailed and deep only); --no-factcheck off
uv run dev/mock_llm.py                     # a fake OpenAI-compatible LLM on :8765 for free end-to-end runs
uv add --group worker <pkg>                # uv, Python 3.12: shared deps in [project], each part's in its dependency group
uv run eval/run.py                         # quality measurement on eval/topics.txt -> eval/results/ (≈10 ₽); see eval/CLAUDE.md
```

The commands work from any folder of the repository: uv finds the project upward, `my_browser` is installed into the environment as an editable package, and `docker compose` finds its file upward too. Root scripts take a path from elsewhere (`uv run ../../research.py` from a part's folder). Don't name a module after a stdlib one (core's job queue is `jobs.py`, not `queue.py`): `python -m` and `python -c` put the current folder first on `sys.path`, so a `queue.py` there shadowed the stdlib's and core did not start from its own folder.

Outputs land in `reports/` as `<date>_<time>_<slug>.{md,docx,pdf,json}`; the `.json` holds query, mode, writer, cost, duration, word count, source URLs, the fact-check, follow-ups and parent. `reports/library.sqlite` numbers the reports, indexes them for search and holds the job queue. The `REPORTS_DIR` env var moves all of that elsewhere; it is read when `my_browser.common.settings` is imported.

There is deliberately no test suite while the project is in active development (the user's call). Verify with throwaway runs: the `check-project` skill (`.claude/skills/`, `/check-project`) imports everything and runs one research end to end for free, with `dev/mock_llm.py` as the LLM; measure search and writing changes with `eval/run.py`. Web UI E2E: `uv run --with playwright` driving the installed Chrome (`channel="chrome"`, headless) against the app. `uv run` wraps the Python process, so to stop a server or simulate a crash kill the process listening on the port, not uv.

## Architecture: four parts

`my_browser/` has one package per part plus `common/`. The parts talk over HTTP with a shared service token (`SERVICE_TOKEN`, else `REPORTS_DIR/.service_token`; `common/auth.py`), so each can be improved and restarted alone:

| Part | Package | Owns | Talks to |
|---|---|---|---|
| core | `core/` | web UI (NiceGUI), API under `/api`, report library, job queue, blocklist | worker (questions, follow-ups, clarification) |
| worker | `worker/` | the research engine (gpt-researcher + our patches), LLM helpers, DOCX/PDF | core (jobs), search (sources, pages) |
| search | `search/` | source selection (`/select`) and page reading with fallbacks (`/read`), read stats | — |
| bot | `bot/` | Telegram | core |

Each of these folders, and `common/`, `eval/` and `proxy/`, has its own CLAUDE.md. It says what the folder does, how to run and check it alone, what other parts rely on (API fields, files, phase and log lines, gpt-researcher internals), and what else to update when one side changes. Read it before changing the folder; a session started in the folder loads it by itself.

- `common/clients.py`: `call(part, method, path)`. Core is always reached at `CORE_URL` (default `http://127.0.0.1:PORT`). Worker and search are reached at `WORKER_URL` / `SEARCH_URL`, or, when unset, in-process through their FastAPI apps (`httpx.ASGITransport`): the same requests without a network hop. A client is opened per call because every research runs in its own event loop.
- All-in-one (`app.py`, also what Railway runs): one process, one port. NiceGUI serves core's pages and `/api` (`app.py` imports NiceGUI only through `core.web`, which first points NiceGUI's login storage at the volume); `start_parts()` starts the worker's job loop in a thread and the bot as a task; worker and search are called in-process; the job loop and the bot call core over localhost. On Railway this keeps memory and cost as before; separate Railway services later need only commands and `*_URL` variables, no code.
- Shared code in `common/` (details in its CLAUDE.md): `settings.py` (paths, `SOURCES`/`WRITERS`/`MODES`, URLs, token, logging), `clients.py`, `auth.py`, `costs.py` (the real cost in rubles), `text.py`, `report.py` (the sections appended to a report), `phases.py` (the worker's progress lines, which core parses), `gptr.py` (upstream's unpatched page fetch, for search).
- Dependencies: `[project]` holds what every part needs (httpx, python-dotenv), dependency groups `core`/`worker`/`search`/`bot` hold the rest, and `tool.uv.default-groups` installs all of them (the Dockerfile's `uv sync --no-dev` too). Each part imports with only its own group (checked 20.10.2026 in throwaway environments); an image for one part would use `uv sync --no-default-groups --group <part>`.
- Other folders: `eval/` measures quality, `dev/` holds the mock LLM, `proxy/` is the Cloudflare Worker for access from Russia (plus `proxy/ru/`, an archived server setup that is not deployed), `static/` the web app's assets, fonts and icons, `searxng/` the local SearXNG's settings.

## Working in one part

The user starts separate Claude Code sessions in one folder (`my_browser/core`, `worker`, `search`, `bot`, `common`, or `eval`, `proxy`), sometimes several at once in this same checkout. A session started in a folder loads this file and the folder's CLAUDE.md and shares the repository's auto memory. Through the folder's `.claude/settings.json` it reads the whole repository without prompts.

- **Where to edit.** Change files outside your folder only when your change needs it:
  - the other side of a contract you change, in the same commit;
  - your item in `plan.md`;
  - your part's lines in this file and in `README.md`.

  Name those files in your report to the user.
- **Contracts.** Keep them compatible: add fields and endpoints rather than rename or remove them. Reports already on the volume keep their old shape. Your folder's «When you change…» table lists who else reads what you change.
- **`common/`** changes every part at once. Adding to it is fine. Before changing what existing code there does, check every user listed in `common/CLAUDE.md`.
- **Dependencies** go into your part's group: `uv add --group <part> <package>`.
  - gpt-researcher, fastapi and uvicorn sit in both the worker and the search groups, so upgrade them for both parts and run the whole check.
  - Never point `--no-default-groups` or `--group` at the shared `.venv`. Syncing it to one group uninstalls the other parts' packages under every running session. Check a single group in a throwaway environment: `UV_PROJECT_ENVIRONMENT=<scratch>/venv-<part> uv run --no-default-groups --group <part> …`.
- **Other sessions** may have uncommitted work in this checkout.
  - Stage and commit only your own files, by path: `git add <paths>`, `git commit -m "…" -- <paths>`.
  - Never run `git add -A`, `git commit -a`, `git stash`, `git checkout` or `git restore` on files that are not yours, `git reset --hard` or `git clean`: they take or destroy someone else's work.
- **Local runs.**
  - Use a throwaway `REPORTS_DIR` (in your scratchpad) and your own ports: `PORT=86xx PUBLIC=0`, `WORKER_PORT`, `SEARCH_PORT`. Ports 8501–8503 and 8765 may be someone else's.
  - Keep `TELEGRAM_BOT_TOKEN` and `TELEGRAM_TOKEN` empty: a second bot poller breaks the bot on Railway.
  - Run one `eval/run.py` at a time: runs share `eval/reports/`.
  - SearXNG is shared too, and parallel researches use up its engines' rate limits together.
- **Before committing**, run the `check-project` skill.
- **Pushing.** A push to `main` deploys to Railway at once (see Deployment).
  - If the new code fails to start, the app is down: with the volume, the old deployment has already stopped.
  - Revert rather than fix forward: `git revert <sha>`, then push.
  - Deploy results: `gh api repos/MrBoomer1981/DEEper/deployments`, then `…/deployments/<id>/statuses`.

## Design (AVEN brandbook)

The UI and PDFs follow the user's brandbook (`aven-brandbook.pdf`, kept locally and gitignored because the repo is public).
- Dark only. Background is night `#14100E`, text is paper `#F2EBE1` (never `#FFF`), bordo `#7A2A35` is used only for fills such as buttons, selected toggles and 3/3 badges (never as text on dark), and amber `#E8A24A` is the single accent per screen (one label, the timer, the tab underline, link underlines).
- There is no fifth color and no blue, green, neon or gradients, apart from the brand's own subtle bordo glow.
- The mobile redesign follows `DESIGN_HANDOFF.md` (tokens, screens, the Дип-Диггер mascot in `static/mascot/`, the graffiti layer, stages and acceptance checklist). Its one exception to the rules here: the graffiti layer may be pure white `#FFFFFF`, as decoration (`aria-hidden`, no pointer events) that the user can switch off.
- Fonts are self-hosted in `static/fonts`: Unbounded only for 2–4 word headings, Onest for text (including long report titles), JetBrains Mono for labels, chips, numbers and URLs (all OFL); Permanent Marker (Apache 2.0, Latin only) and Amatic SC Bold (OFL, Cyrillic) only for graffiti. The web loads WOFF2 subsets (Latin, Cyrillic, punctuation; about 280 KB for all five against 1.4 MB of TTF), made with fontTools (`subset`, then for Unbounded `varLib.instancer` limiting `wght` to 700–900, the only weights the web uses) from the TTFs, which the PDF keeps using; regenerate the WOFF2 when a TTF changes.
- Styles and client scripts are `static/app.css` and `static/app.js`, linked with a content hash (`?v=`) so a deploy is never hidden by the browser cache.
- The app icon is «Корона + D» (`static/deeper-icon.svg`); the PNG icons (Dock/PWA/apple-touch/favicon, the favicon without the crown) are rendered from it with headless Chrome, and `bot-avatar.png` (640×640, for BotFather) from `static/mascot/dip-hello.svg`. The earlier black hole (`deeper-blackhole.svg`) and AVEN mark (`aven-mark.svg`) stay in the repo.
- Brand colors are registered as Quasar colors (`ui.colors(**BRAND_COLORS)`: `bordo`, `paper`, `amber`, `night`), so props like `color=bordo text-color=paper` work.
- WeasyPrint gotchas:
  - `@font-face` only works when a `FontConfiguration` is passed to both `CSS()` and `write_pdf()`.
  - `font-weight` ranges and non-100 steps such as 650 are rejected.

## Deployment

- GitHub: public repo `MrBoomer1981/DEEper`. The pre-2026-10 DEEper code (aiogram + Groq + Tavily) lives in the `legacy` branch. Old history contains a leaked `.env`.
- Railway deploys `main` with the `Dockerfile` and runs `app.py` (the Dockerfile's `CMD`): all four parts in one process, on a single replica, because two Telegram pollers conflict. The image installs the dependencies only (`--no-install-project`) and runs from `/app`, where the code is; `.dockerignore` keeps `eval/`, `dev/`, `proxy/` and `.claude/` out of it.
- The build and deploy settings live in the service's settings on Railway (Settings → Build → Builder = Dockerfile, since 2026-10-20), not in the repo: `railway.json` (Config as Code) was removed because Railway reads it only until 2026-12-01. Avoid `railway config migrate` (Infrastructure as Code) here: its generated file omits the volume and the domain, and `apply` deletes resources the file does not declare.
- From Russian networks Railway's address does not open without a VPN. The phone opens the app through `proxy/`, a Cloudflare Worker; see `proxy/CLAUDE.md`. It does not open on mobile internet, where Russian providers let a connection to Cloudflare carry only its first ~16 KB; a server in Russia for that (`proxy/ru/`) is ready but archived (plan.md, «Архив»). Behind the proxy the app must keep to relative redirects and links: an absolute URL built from the request's host names the Railway address. Per-IP logic (the login rate limit) sees a proxy's address, not the phone's.
- There is no SearXNG on Railway. `SOURCES["web"]` becomes `tavily` automatically when `TAVILY_API_KEY` is set and `SEARX_URL` is not; `WEB_RETRIEVER` overrides this.
- Reports, the job queue and read stats persist only if a volume is mounted at `/app/reports`. Because of the volume, a redeploy stops the old deployment before the new one starts: a short downtime each time, and no fallback if the new one fails.
- The container runs in UTC, and on Linux WeasyPrint finds pango without the DYLD workaround.
- Build the image locally with `docker build -t deeper-check .` (about 6 min, ~1.9 GB). To test it against the host's SearXNG and mock LLM, use `host.docker.internal`.

## Non-obvious behavior

- DeepSeek through gpt-researcher's `deepseek` provider (ChatOpenAI pointed at api.deepseek.com):
  - Thinking mode is disabled via `LLM_KWARGS.extra_body.thinking`; otherwise every call reasons, which makes it slower and more expensive.
  - langchain-openai sends `max_completion_tokens`, but DeepSeek reads only `max_tokens` (default 8K in non-thinking mode). That is why `max_tokens` is duplicated inside `extra_body`.
- Embeddings are intentionally not configured. With `CONTEXT_FILTER=keyword` and no `OPENAI_API_KEY`, gpt-researcher falls back to keyword ranking. If `OPENAI_API_KEY` is present in the environment, it silently switches to OpenAI embeddings (which is why the mock LLM answers `/embeddings`). Upstream's keyword filter is English-only; `worker/stemming.py` adds Russian morphology.
- Report-writing calls use `stream=True`. An OpenAI-compatible endpoint or mock without SSE streaming gets each call retried 10× with backoff, which looks like a hang.
- SearXNG (`searxng/settings.yml`) must keep the `json` format enabled. From this machine, only `google cse` and `yahoo` answer relevantly. bing returns off-topic junk for Cyrillic queries (Nickelodeon, Halloween stores, banks), and those pages also slow scraping. google, yandex, qwant, duckduckgo, brave and wikipedia return captcha or rate-limit errors. The `semantic_scholar` retriever gets 429 without a key and has no key support, so it is excluded from `--sources`.
- In quick and deep modes, gpt-researcher writes the references itself. Citation style varies between runs: sometimes inline markdown links, sometimes APA author-date with links only at the end.
- `GPTResearcher.get_costs()` prices unknown models at OpenAI defaults, so it is wrong for DeepSeek. Real spend is on the DeepSeek dashboard.

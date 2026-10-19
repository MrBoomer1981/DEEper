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
uv add --group worker <pkg>                # uv, Python 3.12: shared deps in [project], each part's in its dependency group
uv run eval/run.py                         # quality measurement on eval/topics.txt -> eval/results/ (≈10 ₽: quick reports, Flash)
uv run eval/run.py --topics 1,4 --label exa   # a subset, named; --mode/--writer/--sources/--pairs as in research.py
```

Outputs land in `reports/` as `<date>_<time>_<slug>.{md,docx,pdf,json}`; the `.json` holds query, mode, writer, cost, duration, word count, source URLs, follow-ups and parent. `reports/library.sqlite` numbers the reports, indexes them for search and holds the job queue. The `REPORTS_DIR` env var moves all of that elsewhere; it is read when `my_browser.common.settings` is imported. There is deliberately no test suite while the project is in active development (the user's call): verify with throwaway runs, and measure search changes with `eval/run.py`. Web UI E2E: `uv run --with playwright` driving the installed Chrome (`channel="chrome"`, headless) against `uv run app.py`. For free end-to-end checks, set `FAST_LLM`, `SMART_LLM`, `STRATEGIC_LLM` to `openai:mock` plus `OPENAI_BASE_URL`/`OPENAI_API_KEY` for a local OpenAI-compatible mock; the mock must support SSE streaming and `/embeddings`. Environment variables beat the per-run config (see below), so the mock is used whatever writer is picked. Run local checks with a temporary `REPORTS_DIR` and empty `TELEGRAM_BOT_TOKEN`/`TELEGRAM_TOKEN`: a second bot poller conflicts with the one on Railway. `uv run` wraps the Python process, so to simulate a crash kill the process listening on the port, not uv.

## Architecture: four parts

`my_browser/` has one package per part plus `common/`. The parts talk over HTTP with a shared service token (`SERVICE_TOKEN`, else `REPORTS_DIR/.service_token`; `common/auth.py`), so each can be improved and restarted alone:

| Part | Package | Owns | Talks to |
|---|---|---|---|
| core | `core/` | web UI (NiceGUI), API under `/api`, report library, job queue, blocklist | worker (questions, follow-ups, clarification) |
| worker | `worker/` | the research engine (gpt-researcher + our patches), LLM helpers, DOCX/PDF | core (jobs), search (sources, pages) |
| search | `search/` | source selection (`/select`) and page reading with fallbacks (`/read`), read stats | — |
| bot | `bot/` | Telegram | core |

Each part's package, and `common/`, has its own CLAUDE.md: what the part relies on and what relies on it (API fields, files, log lines, gpt-researcher internals) and what else to update when one side changes. Read it before changing the part.

- `common/clients.py`: `call(part, method, path)`. Core is always reached at `CORE_URL` (default `http://127.0.0.1:PORT`). Worker and search are reached at `WORKER_URL` / `SEARCH_URL`, or, when unset, in-process through their FastAPI apps (`httpx.ASGITransport`): the same requests without a network hop. A client is opened per call because every research runs in its own event loop.
- All-in-one (`app.py`, also what Railway runs): one process, one port. NiceGUI serves core's pages and `/api` (`app.py` imports NiceGUI only through `core.web`, which first points NiceGUI's login storage at the volume); `start_parts()` starts the worker's job loop in a thread and the bot as a task; worker and search are called in-process; the job loop and the bot call core over localhost. On Railway this keeps memory and cost as before; separate Railway services later need only commands and `*_URL` variables, no code.
- Shared code in `common/`: `settings.py` (paths, `SOURCES`/`WRITERS`/`MODES`, URLs, token, `configure_logging()`, which quiets httpx because the job loop polls core every few seconds), `costs.py`, `text.py` (`host`, `readable_url`, `slugify`, `rub_text`), `report.py` (the follow-ups section), `gptr.py` (the unpatched `Scraper.extract_data_from_url`, captured before the worker patches it, so that search can still fetch pages in the same process).
- Dependencies: `[project]` holds what every part needs (httpx, python-dotenv), dependency groups `core`/`worker`/`search`/`bot` hold the rest, and `tool.uv.default-groups` installs all of them (the Dockerfile's `uv sync --no-dev` too). An image for one part would use `uv sync --no-default-groups --group <part>`.

### core
- `core/queue.py`: jobs in SQLite (`jobs` table in `library.sqlite`). Statuses go queued → running → done | error | interrupted. The worker claims the oldest queued job and reports progress (phase, queries, accepted sources with scores, the last 200 log lines, cost), which doubles as its heartbeat. A worker that (re)starts says hello, and jobs running under another worker id become interrupted; a running job silent for 5 minutes is interrupted too. Interrupted and failed jobs go back to the queue as they were (`restart`). Telegram jobs keep `origin_ref` (chat, status message) so the bot can update that message after a restart.
- `core/service.py`: operations shared by the pages (direct calls) and `core/api.py` (bot, worker): submit, claim (adds the blocklist), store a finished report (`Library.store`, base64 files from the worker), report view, and the worker-backed `ask` / `followups` / `clarify` / `refine`.
- `core/web.py` (NiceGUI 3, the former `app.py`):
  - Pages: `/` (new research; takes `topic`, `mode`, `parent` query parameters), `/job/{id}` (polls the queue every second; error and interrupted states offer restart), `/report/{id}` (tabs for report, sources with block buttons, Q&A; «Что исследовать дальше» cards, continuations and related reports), `/login`. `/files/{id}/{pdf|docx|md}` serves downloads.
  - Auth: `AuthMiddleware` is active when it listens publicly (`PORT` is set, as on Railway) or when `APP_PASSWORD` is set. `/api/` is open to it and checks the service token itself.
  - `NICEGUI_STORAGE_PATH` and the storage secret live in `reports/` (the volume), so logins survive redeploys.
  - Icon-only buttons carry `aria-label`s. The PWA manifest and icons are in `static/`, which is what Safari "Add to Dock" uses.
- `core/library.py`: SQLite with an FTS5 table. Report files are the source of truth. `sync()` re-indexes `reports/<date>_*.json` (other JSON there, such as `read_stats.json` and `.qa.json` files, is skipped), so the DB can be deleted and rebuilt. Search uses per-word prefix matching so that Russian word forms match.
- Research threads: a report started from another one stores `parent` = the parent's file name, not its library id, because ids change when the DB is rebuilt. The library's `parent` column is added by `ALTER TABLE` on older DBs; `children()`, `by_name()` and `related()` (OR prefix match on titles) feed the report page.
- `core/qa.py`: Q&A history next to each report as `<name>.qa.json`. When the report lacks the answer, the worker's model ends with an «ИССЛЕДОВАТЬ: тема» line, which is cut from the answer and kept as `research`; both UIs offer to run it.
- `core/blocklist.py`: `reports/blocked_domains.txt` (on the Railway volume), managed from the web dialog and the bot's `/block`, `/unblock`, `/blocked`; handed to each research with its job.

### worker
- `worker/runner.py`: the job loop. One research at a time, in a plain thread. Per job it sets the cost meter and the blocklist ContextVars (`asyncio.run` copies them into the research), runs `pipeline.research()`, asks for follow-ups, renders the files and hands them in (with retries, since core may be restarting). A handler on gpt-researcher's `scraper` logger collects the planned queries and the `Added source url (score)` lines; a ticker thread sends progress every few seconds when it changed and every 30 s as a heartbeat.
- `worker/pipeline.py`: `run_settings()` (config.json plus the run's sources, writer, words and language) written by `config_file()` to a temporary file for each run, so nothing process-wide changes during a research. Configuration precedence in gpt-researcher is **env vars > config file > library defaults**, so any key from `config.json` can still be overridden in `.env`, and an environment variable beats the run's choice too. Also: `preflight()` (a 1-token DeepSeek call and a SearXNG JSON ping, so a bad key, an empty balance or a stopped container fails in seconds; raises `PreflightError`), `research()` (one of three modes) and `render()` (md/json/docx/pdf bytes; core stores them).
- Modes: `quick` = `research_report`; `deep` = gpt-researcher's recursive `deep` report (`DEEP_RESEARCH_*` keys); `detailed` = `worker/detailed.py`, a port of upstream `backend/report_type/detailed_report`, which is **not shipped in the pip package**. It runs one broad research pass, then each subtopic is researched and written as its own section (each at least `TOTAL_WORDS` long), strictly one after another so that each section sees the ones already written; then the intro, TOC, conclusion and references are stitched together. With Pro writing it takes about 18 minutes. Its `## Источники` list is built from the links cited inline, not from every visited URL; if fewer than 3 links are cited (APA author-date style), it falls back to all visited URLs.
- `worker/hooks.py` patches gpt-researcher in the worker process: every `*Search` retriever's `search()` remembers titles and snippets in `SEARCH_META`; `ResearchConductor._get_new_urls` (where search results become research sources, and later the references) asks search's `/select`, passing candidates with titles and snippets, per-domain counts of the research so far (`visited_urls`, which detailed sub-researchers share) and the blocklist, and merges the rating's usage into the job's meter; `Scraper.extract_data_from_url` goes through search's `/read`. URLs are re-checked against `visited_urls` after the await, because parallel sub-queries race. `SOURCE_FILTER=off` disables the selection. The search engines themselves still run inside gpt-researcher here.
- `worker/prompts.py` makes search queries English-first while the report stays in `LANGUAGE`. It registers the `english_first` prompt family (`PROMPT_FAMILY` in `config.json`) and monkeypatches two upstream spots:
  - `query_processing.generate_sub_queries`: upstream `plan_research_outline()` doesn't pass `prompt_family`, so a custom family never reached sub-query generation.
  - `DeepResearchSkill.generate_search_queries`: its query prompt is inline, so it gets the language rule and today's date.
- `worker/stemming.py` swaps gpt-researcher's BM25 tokenizer (`context/lexical.py`) for one with the Snowball Russian stemmer (from nltk) and Russian stopwords; Latin tokens go through upstream's stemmer unchanged. `pipeline.research()` imports `hooks`, `prompts` and `stemming` for their side effects.
- `worker/suggest.py` holds the cheap FAST_LLM suggestions: follow-ups («Что исследовать дальше», 5 topics, deeper/adjacent/verify, each with `why` and a recommended mode, from the report's outline, conclusions and limitation sections; a failure returns `[]` and never blocks saving) and clarification («Уточнить»: `questions()`, `refine()`). `render()` puts follow-ups into the JSON and as the last `## Что исследовать дальше` section into the documents; the web view strips that section (`common/report.body()`) and renders cards instead. Older reports get topics on demand («Предложить темы»), in the JSON only.
- `worker/ask.py` answers a question from a report's text with `SMART_LLM`; `worker/api.py` exposes `/ask`, `/followups`, `/clarify`, `/refine` for core.
- `worker/export.py`: DOCX is rendered in-process (mistune → htmldocx). The PDF is rendered by WeasyPrint **in a child process** (`python -m my_browser.worker.export`) with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`, because WeasyPrint needs Homebrew's pango (`brew install pango`) and dyld reads that variable only at process start.
- Writer: `SMART_LLM` (report text and Q&A) defaults to `deepseek-v4-pro`; FAST and STRATEGIC stay on `deepseek-flash`. `--writer`, the bot default and the web toggle pick `WRITERS[...]` through `run_settings(writer=...)`.
- `common/costs.py` meters real cost. It wraps `GenericLLMProvider.get_chat_response` and adds usage tokens to the `Meter` in the `costs.current` ContextVar: the job loop sets it per research, `costs.metered()` isolates a single call (a question, a clarification, search's rating, which is returned as usage and merged into the job's meter).
  - DeepSeek prices (peak 01–04 and 06–10 UTC on weekdays, half price otherwise) and the CBR USD/RUB rate (cached 12 h; `USD_RUB` overrides) go into `cost` in each report's JSON and into `cost_rub` per Q&A. The UI and the bot show them.
  - Streamed calls report usage only because `LLM_KWARGS.stream_usage` is true.
  - `finish_reason == "length"` is logged and counted as `truncated_calls`.
- URLs are made readable (`common/text.readable_url`): percent-encoded Cyrillic is decoded, ASCII escapes are kept. Encoded slugs cost 3–4 tokens per letter and made long reports hit the output cap mid-reference (16k back then, now 32k).

### search
- `search/sources.py` (`/select`): one FAST_LLM call per batch scores every candidate 0–3 for relevance to the topic, credibility and freshness, seeing URL, title and snippet. Static tiers (`DOMAIN_SCORES`, gov/edu) only raise a relevant source and never rescue an off-topic one; arXiv's keyword search returns plenty of unrelated physics. Selection: 0 is dropped; 1 is kept only if the batch has nothing ≥2, and then at most 3; at most `PER_DOMAIN_QUERY`=2 per domain per batch and `PER_DOMAIN_RESEARCH`=5 per research; at most `MAX_SOURCES_PER_QUERY`=8; domains that stay unreadable lose a point.
- `search/reader.py` (`/read`): the plain fetch (the unpatched gpt-researcher scraper), then free fallbacks: Europe PMC full text (PubMed Central links), an open-access copy found via OpenAlex (URLs that carry a DOI), Jina Reader (`r.jina.ai`, paced to 20 a minute unless `JINA_API_KEY` is set), and finally the OpenAlex abstract. Fallback text is capped at 150k characters.
  - gpt-researcher's `WorkerPool` holds an `asyncio.Semaphore`, which binds to one event loop, so each loop gets its own `Scraper`; they share one thread pool.
  - Per-domain outcomes go to `REPORTS_DIR/read_stats.json` (search's own state). A domain whose plain fetch failed at least 80% of 3+ tries skips it.
  - Its INFO lines (`Read via <how>: <url>`, `Unreadable after fallbacks: <url>`) feed eval's «Спасено» and «Не скачалось». Upstream's own «treating as fetch failure» warning fires before the fallbacks run, so it does not mean the page was lost.

### bot
- `bot/telegram.py` (aiogram 3) uses core's API only; report files are downloaded from core and sent as `BufferedInputFile`.
  - Access is limited to `TELEGRAM_ALLOWED_USERS` through an outer middleware; anyone else is only told their ID. `TELEGRAM_TOKEN` (the DEEper v1 name) works as a fallback for `TELEGRAM_BOT_TOKEN`.
  - A plain-text message is treated as a topic and answered with mode and source buttons. A research is a job in core; a status message follows it every few seconds.
  - Replying to any message that contains `#N`, or using `/ask N`, answers from report N's text.
  - Callback data prefixes: `m:`/`s:` mode and sources, `c:`/`a:` clarification, `f:<report id>:<index>` a follow-up topic, `r:<report id>:<qa index>` a question's research topic, `x:<job id>` restart a failed or interrupted job. Topics are read back through the API, because Telegram limits callback data to 64 bytes. `pending` holds the offered topics, keyed by the message that offered them.
  - Every minute it looks for Telegram jobs interrupted by a restart and edits their status message to offer a restart.

### eval
- `run.py` runs each topic of `topics.txt` through `pipeline.research()` (search in-process) with `REPORTS_DIR=eval/reports`, a library of its own, and the app's blocklist.
- `metrics.py` computes source and citation stats from the saved report and from the captured log lines of the `scraper`, `gpt_researcher.scraper.scraper` and `my_browser.search.reader` loggers (accepted sources with scores, fetch failures, rescues).
- Two FAST_LLM judges, billed apart from the reports: off-topic sources by URL and title (SearXNG results carry no titles, so the snippet stands in), and FACT-style citation accuracy. For the latter, statement→link pairs are sampled with a per-topic seed, the page is re-fetched, and the 4 chunks sharing the most numbers, Latin names and stems are judged. Known flaw: Russian statements without numbers or Latin names get the wrong chunks of English pages.
- Results go to `eval/results/<date>_<label>.md|json`, each next to the averages of the last run made the same way (same mode, writer and sources). In detailed mode the report's source list is its cited links (`DetailedReport.source_urls()`), so `in_report` is 100% there by construction.

## Design (AVEN brandbook)

The UI and PDFs follow the user's brandbook (`aven-brandbook.pdf`, kept locally and gitignored because the repo is public).
- Dark only. Background is night `#14100E`, text is paper `#F2EBE1` (never `#FFF`), bordo `#7A2A35` is used only for fills such as buttons, selected toggles and 3/3 badges (never as text on dark), and amber `#E8A24A` is the single accent per screen (one label, the timer, the tab underline, link underlines).
- There is no fifth color and no blue, green, neon or gradients, apart from the brand's own subtle bordo glow.
- Fonts are self-hosted in `static/fonts` (OFL): Unbounded only for 2–4 word headings, Onest for text (including long report titles), JetBrains Mono for labels, chips, numbers and URLs.
- Header and login use the AVEN mark (`static/aven-mark.svg`, copied verbatim from the user's aven-logo artifact).
- The app's avatar is the user-requested black hole on a red-black field: `static/deeper-blackhole.svg`, in brand colors only. All PNG icons (Dock/PWA/apple-touch/favicon) and `bot-avatar.png` (640×640, for BotFather) are rendered from it with headless Chrome.
- Brand colors are registered as Quasar colors (`ui.colors(**BRAND_COLORS)`: `bordo`, `paper`, `amber`, `night`), so props like `color=bordo text-color=paper` work.
- WeasyPrint gotchas:
  - `@font-face` only works when a `FontConfiguration` is passed to both `CSS()` and `write_pdf()`.
  - `font-weight` ranges and non-100 steps such as 650 are rejected.

## Deployment

- GitHub: public repo `MrBoomer1981/DEEper`. The pre-2026-10 DEEper code (aiogram + Groq + Tavily) lives in the `legacy` branch. Old history contains a leaked `.env`.
- Railway deploys `main` with the `Dockerfile` (`railway.json`, a single replica, because two Telegram pollers conflict) and runs `app.py`: all four parts in one process.
- Railway reads `railway.json` (Config as Code) only until 2026-12-01, and the service's own build setting is still Railpack. Before removing the file, switch the setting: `railway environment edit --service-config DEEPer build.builder DOCKERFILE --service-config DEEPer build.dockerfilePath Dockerfile`. Avoid `railway config migrate` (IaC) here: its generated file omits the volume and the domain, and `apply` deletes resources the file does not declare.
- There is no SearXNG on Railway. `SOURCES["web"]` becomes `tavily` automatically when `TAVILY_API_KEY` is set and `SEARX_URL` is not; `WEB_RETRIEVER` overrides this.
- Reports, the job queue and read stats persist only if a volume is mounted at `/app/reports`.
- The container runs in UTC, and on Linux WeasyPrint finds pango without the DYLD workaround.
- Build the image locally with `docker build -t my-browser-bot .` (about 6 min, ~1.9 GB). To test it against the host's SearXNG and mock LLM, use `host.docker.internal`.

## Non-obvious behavior

- DeepSeek through gpt-researcher's `deepseek` provider (ChatOpenAI pointed at api.deepseek.com):
  - Thinking mode is disabled via `LLM_KWARGS.extra_body.thinking`; otherwise every call reasons, which makes it slower and more expensive.
  - langchain-openai sends `max_completion_tokens`, but DeepSeek reads only `max_tokens` (default 8K in non-thinking mode). That is why `max_tokens` is duplicated inside `extra_body`.
- Embeddings are intentionally not configured. With `CONTEXT_FILTER=keyword` and no `OPENAI_API_KEY`, gpt-researcher falls back to keyword ranking. If `OPENAI_API_KEY` is present in the environment, it silently switches to OpenAI embeddings. Upstream's keyword filter is English-only; `worker/stemming.py` adds Russian morphology.
- Report-writing calls use `stream=True`. An OpenAI-compatible endpoint or mock without SSE streaming gets each call retried 10× with backoff, which looks like a hang.
- SearXNG (`searxng/settings.yml`) must keep the `json` format enabled. From this machine, only `google cse` and `yahoo` answer relevantly. bing returns off-topic junk for Cyrillic queries (Nickelodeon, Halloween stores, banks), and those pages also slow scraping. google, yandex, qwant, duckduckgo, brave and wikipedia return captcha or rate-limit errors. The `semantic_scholar` retriever gets 429 without a key and has no key support, so it is excluded from `--sources`.
- In quick and deep modes, gpt-researcher writes the references itself. Citation style varies between runs: sometimes inline markdown links, sometimes APA author-date with links only at the end.
- `GPTResearcher.get_costs()` prices unknown models at OpenAI defaults, so it is wrong for DeepSeek. Real spend is on the DeepSeek dashboard.

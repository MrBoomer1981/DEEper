# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal deep-research engine that writes long reports: the `gpt-researcher` pip library for orchestration, a local SearXNG for free web search, and DeepSeek (`deepseek-flash`) as the LLM. The user writes in Russian; reports default to Russian.

## Commands

```bash
docker compose up -d                       # start local SearXNG on 127.0.0.1:8080 (needs SEARXNG_SECRET in .env)
uv run research.py "тема"                  # detailed report (default mode) -> reports/
uv run research.py "тема" --mode deep --sources all --words 2500
uv run research.py --help
uv run app.py                              # web UI at 127.0.0.1:8501 (+ the bot if a token is set); 8080 is SearXNG
uv run bot.py                              # Telegram bot only (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS in .env)
uv add <pkg>                               # dependencies are managed by uv (Python 3.12)
```

Outputs land in `reports/` as `<date>_<slug>.{md,docx,pdf,json}`; the `.json` holds query, mode, duration, word count and source URLs. `reports/library.sqlite` numbers the reports and indexes them for search. There are no tests yet. Web UI E2E: `uv run --with playwright` driving the installed Chrome (`channel="chrome"`, headless) against `uv run app.py`. For free end-to-end checks, point all three LLMs at a local OpenAI-compatible mock (`FAST_LLM=openai:mock`, etc., plus `OPENAI_BASE_URL`); the mock must support SSE streaming and `/embeddings`.

## Architecture

- `my_browser/pipeline.py` is shared by the CLI (`research.py`) and the bot (`bot.py`):
  - `preflight()` makes a 1-token DeepSeek call and pings SearXNG, so a bad key, an empty balance or a stopped container fails in seconds. It raises `PreflightError`.
  - `research()` runs one of three modes.
  - `save()` writes the files and adds the report to the library.
- Configuration precedence in gpt-researcher: **env vars > `config.json` > library defaults**. Per-run options (`RETRIEVER`, `TOTAL_WORDS`, `LANGUAGE`) are applied through `env_overrides()`, which mutates the process environment and restores it afterwards. Two researches must therefore never run concurrently in one process; the bot serializes them with a lock. Any key in `config.json` can be overridden from `.env`.
- `my_browser/jobs.py` is the research queue shared by the web app and the bot.
  - Each job runs in its own thread under `RESEARCH_LOCK` (env overrides are process-wide).
  - Progress comes from pipeline phases plus a handler on gpt-researcher's `scraper` logger, which parses the planned queries and the `Added source url (score)` lines.
  - `JOBS` lives in memory, so it is lost on restart; reports persist.
- `app.py` (NiceGUI 3) is the web UI.
  - Pages: `/` (new research), `/job/{id}` (live progress, polled by `ui.timer`), `/report/{id}` (tabs for report, sources with block buttons, and Q&A), `/login`. `/files/{id}/{pdf|docx|md}` serves downloads.
  - Auth: `AuthMiddleware` is active when it listens publicly (`PORT` is set, as on Railway) or when `APP_PASSWORD` is set.
  - `NICEGUI_STORAGE_PATH` and the storage secret live in `reports/` (the volume), so logins survive redeploys.
  - On startup it runs `bot.start(handle_signals=False)` when a Telegram token exists. Railway runs `app.py`: one process, one library.
  - Icon-only buttons carry `aria-label`s. The PWA manifest and icons are in `static/`, which is what Safari "Add to Dock" uses.
- Q&A history is stored next to each report as `<name>.qa.json` (`my_browser/ask.py`); `Library.sync()` skips these files. `ask()` returns the stored item. When the report lacks the answer, the model ends with an «ИССЛЕДОВАТЬ: тема» line, which is cut from the answer and kept as `research`; both UIs offer to run it.
- `my_browser/suggest.py` holds the cheap FAST_LLM suggestions around a run:
  - Follow-ups («Что исследовать дальше»): after `research()`, the job and the CLI ask for 5 topics (deeper/adjacent/verify, each with `why` and a recommended mode) based on the report's outline, conclusions and limitation sections. Their cost is part of the report's cost. `save()` stores them as `followups` in the JSON and appends them to md/docx/pdf as the last `## Что исследовать дальше` section; the web view strips that section with `suggest.body()` and renders cards instead. A failure returns `[]` and never blocks saving. Older reports get topics on demand («Предложить темы», `followups_for()`), in the JSON only.
  - Clarification («Уточнить»): `questions()` and `refine()`, each metered with `costs.metered()`.
- Research threads: a report started from another one stores `parent` = the parent's file name, not its library id, because ids change when the DB is rebuilt. The library's `parent` column is added by `ALTER TABLE` on older DBs; `children()`, `by_name()` and `related()` (OR prefix match on titles) feed the report page. The `/` page takes `topic`, `mode` and `parent` (report id) query parameters, and follow-up cards link there.
- `my_browser/library.py`: SQLite with an FTS5 table. Report files are the source of truth. `sync()` re-indexes `reports/*.json`, so the DB can be deleted and rebuilt. Search uses per-word prefix matching so that Russian word forms match.
- `bot.py` (aiogram 3):
  - Access is limited to `TELEGRAM_ALLOWED_USERS` through an outer middleware; anyone else is only told their ID.
  - A plain-text message is treated as a topic and answered with mode and source buttons.
  - Each research runs in a worker thread with its own event loop (`asyncio.to_thread` + `asyncio.run`), so the bot stays responsive. Progress is passed as a plain string that a ticker task renders into the status message every few seconds.
  - Replying to any message that contains `#N`, or using `/ask N`, answers from report N's text (`my_browser/ask.py`).
  - Callback data prefixes: `m:`/`s:` mode and sources, `c:`/`a:` clarification, `f:<report id>:<index>` a follow-up topic, `r:<report id>:<qa index>` a question's research topic. Topics are read back from the report's JSON or `.qa.json`, because Telegram limits callback data to 64 bytes. `pending` holds the offered topics, keyed by the message that offered them.
- Modes: `quick` = `research_report`; `deep` = gpt-researcher's recursive `deep` report (`DEEP_RESEARCH_*` keys); `detailed` = `my_browser/detailed.py`, a port of upstream `backend/report_type/detailed_report`, which is **not shipped in the pip package**. It runs one broad research pass, then each subtopic is researched and written as its own section (each at least `TOTAL_WORDS` long), then the intro, TOC, conclusion and references are stitched together.
- `my_browser/prompts.py` makes search queries English-first while the report stays in `LANGUAGE`. It registers the `english_first` prompt family (`PROMPT_FAMILY` in `config.json`) and monkeypatches two upstream spots:
  - `query_processing.generate_sub_queries`: upstream `plan_research_outline()` doesn't pass `prompt_family`, so a custom family never reached sub-query generation.
  - `DeepResearchSkill.generate_search_queries`: its query prompt is inline, so it gets the language rule and today's date.
  - `pipeline.research()` imports the module for these side effects.
- `my_browser/sources.py` selects sources before scraping. It patches every `*Search` retriever's `search()` to remember titles and snippets in `SEARCH_META`, and replaces `ResearchConductor._get_new_urls`, where search results become research sources and later the references.
  - One `FAST_LLM` call per batch scores every candidate 0–3 for relevance to the topic, credibility and freshness, seeing URL, title and snippet. Static tiers only raise a relevant source and never rescue an off-topic one; arXiv's keyword search returns plenty of unrelated physics.
  - Selection: 0 is dropped; 1 is kept only if the batch has nothing ≥2, and then at most 3; at most `PER_DOMAIN_QUERY`=2 per domain per batch and `PER_DOMAIN_RESEARCH`=5 per research (counted from `visited_urls`, which detailed sub-researchers share); at most `MAX_SOURCES_PER_QUERY`=8.
  - URLs are re-checked against `visited_urls` after the await, because parallel sub-queries race.
  - The user blocklist is `reports/blocked_domains.txt` (on the Railway volume), managed by the bot's `/block`, `/unblock` and `/blocked`.
  - `SOURCE_FILTER=off` disables the filter.
- `my_browser/costs.py` meters real cost. It wraps `GenericLLMProvider.get_chat_response` and adds usage tokens to the `Meter` in the `costs.current` ContextVar, which a job sets in its worker thread (`asyncio.run` copies it) and `ask()` sets per question.
  - DeepSeek prices (peak 01–04 and 06–10 UTC on weekdays, half price otherwise) and the CBR USD/RUB rate (cached 12 h; `USD_RUB` overrides) go into `cost` in each report's JSON and into `cost_rub` per Q&A. The UI and the bot show them.
  - Streamed calls report usage only because `LLM_KWARGS.stream_usage` is true.
  - `finish_reason == "length"` is logged and counted as `truncated_calls`.
- Writer: `SMART_LLM` (report text and Q&A) defaults to `deepseek-v4-pro`; FAST and STRATEGIC stay on `deepseek-flash`. `--writer`, the bot default and the web toggle pick `WRITERS[...]` through `overrides(writer=...)`. The writer override also beats a mock `SMART_LLM` env, so offline tests must point `pipeline.WRITERS` at the mock too, or they spend real money.
- URLs are made readable (`sources.readable_url`): percent-encoded Cyrillic is decoded, ASCII escapes are kept. Encoded slugs cost 3–4 tokens per letter and made long reports hit the output cap mid-reference (16k back then, now 32k).
- `my_browser/stemming.py` swaps gpt-researcher's BM25 tokenizer (`context/lexical.py`) for one with the Snowball Russian stemmer (from nltk) and Russian stopwords; Latin tokens go through upstream's stemmer unchanged.
- In the detailed report, the `## Источники` list is built from the links cited inline, not from every visited URL. If fewer than 3 links are cited (APA author-date style), it falls back to all visited URLs.
- `my_browser/export.py`: DOCX is rendered in-process (mistune → htmldocx). The PDF is rendered by WeasyPrint **in a child process** with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`, because WeasyPrint needs Homebrew's pango (`brew install pango`) and dyld reads that variable only at process start.

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
- Railway deploys `main` with the `Dockerfile` (`railway.json`, a single replica, because two Telegram pollers conflict).
- Railway reads `railway.json` (Config as Code) only until 2026-12-01, and the service's own build setting is still Railpack. Before removing the file, switch the setting: `railway environment edit --service-config DEEPer build.builder DOCKERFILE --service-config DEEPer build.dockerfilePath Dockerfile`. Avoid `railway config migrate` (IaC) here: its generated file omits the volume and the domain, and `apply` deletes resources the file does not declare.
- There is no SearXNG on Railway. `SOURCES["web"]` becomes `tavily` automatically when `TAVILY_API_KEY` is set and `SEARX_URL` is not; `WEB_RETRIEVER` overrides this.
- The bot also accepts `TELEGRAM_TOKEN` (the DEEper v1 name) as a fallback for `TELEGRAM_BOT_TOKEN`.
- Reports persist only if a volume is mounted at `/app/reports`.
- The container runs in UTC, and on Linux WeasyPrint finds pango without the DYLD workaround.
- Build the image locally with `docker build -t my-browser-bot .` (about 6 min, ~1.9 GB). To test it against the host's SearXNG and mock LLM, use `host.docker.internal`.

## Non-obvious behavior

- DeepSeek through gpt-researcher's `deepseek` provider (ChatOpenAI pointed at api.deepseek.com):
  - Thinking mode is disabled via `LLM_KWARGS.extra_body.thinking`; otherwise every call reasons, which makes it slower and more expensive.
  - langchain-openai sends `max_completion_tokens`, but DeepSeek reads only `max_tokens` (default 8K in non-thinking mode). That is why `max_tokens` is duplicated inside `extra_body`.
- Embeddings are intentionally not configured. With `CONTEXT_FILTER=keyword` and no `OPENAI_API_KEY`, gpt-researcher falls back to keyword ranking. If `OPENAI_API_KEY` is present in the environment, it silently switches to OpenAI embeddings. Upstream's keyword filter is English-only; `my_browser/stemming.py` adds Russian morphology.
- Report-writing calls use `stream=True`. An OpenAI-compatible endpoint or mock without SSE streaming gets each call retried 10× with backoff, which looks like a hang.
- SearXNG (`searxng/settings.yml`) must keep the `json` format enabled. From this machine, only `google cse` and `yahoo` answer relevantly. bing returns off-topic junk for Cyrillic queries (Nickelodeon, Halloween stores, banks), and those pages also slow scraping. google, yandex, qwant, duckduckgo, brave and wikipedia return captcha or rate-limit errors. The `semantic_scholar` retriever gets 429 without a key and has no key support, so it is excluded from `--sources`.
- In quick and deep modes, gpt-researcher writes the references itself. Citation style varies between runs: sometimes inline markdown links, sometimes APA author-date with links only at the end.
- `GPTResearcher.get_costs()` prices unknown models at OpenAI defaults, so it is wrong for DeepSeek. Real spend is on the DeepSeek dashboard.

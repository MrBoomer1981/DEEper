# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal deep-research engine that writes long reports: the `gpt-researcher` pip library for orchestration, a local SearXNG for free web search, and DeepSeek (`deepseek-flash`) as the LLM. The user writes in Russian; reports default to Russian.

## Commands

```bash
docker compose up -d                       # start local SearXNG on 127.0.0.1:8080 (needs SEARXNG_SECRET in .env)
uv run research.py "тема"                  # detailed report (default mode) -> reports/
uv run research.py "тема" --mode deep --sources all --words 2500
uv run research.py --help
uv run bot.py                              # Telegram bot (TELEGRAM_BOT_TOKEN, TELEGRAM_ALLOWED_USERS in .env)
uv add <pkg>                               # dependencies are managed by uv (Python 3.12)
```

Outputs land in `reports/` as `<date>_<slug>.{md,docx,pdf,json}`; the `.json` holds query, mode, duration, word count and source URLs. `reports/library.sqlite` numbers the reports and indexes them for search. There are no tests yet. For free end-to-end checks, point all three LLMs at a local OpenAI-compatible mock (`FAST_LLM=openai:mock`, etc., plus `OPENAI_BASE_URL`); the mock must support SSE streaming and `/embeddings`.

## Architecture

- `my_browser/pipeline.py` is shared by the CLI (`research.py`) and the bot (`bot.py`):
  - `preflight()` makes a 1-token DeepSeek call and pings SearXNG, so a bad key, an empty balance or a stopped container fails in seconds. It raises `PreflightError`.
  - `research()` runs one of three modes.
  - `save()` writes the files and adds the report to the library.
- Configuration precedence in gpt-researcher: **env vars > `config.json` > library defaults**. Per-run options (`RETRIEVER`, `TOTAL_WORDS`, `LANGUAGE`) are applied through `env_overrides()`, which mutates the process environment and restores it afterwards. Two researches must therefore never run concurrently in one process; the bot serializes them with a lock. Any key in `config.json` can be overridden from `.env`.
- `my_browser/library.py`: SQLite with an FTS5 table. Report files are the source of truth. `sync()` re-indexes `reports/*.json`, so the DB can be deleted and rebuilt. Search uses per-word prefix matching so that Russian word forms match.
- `bot.py` (aiogram 3):
  - Access is limited to `TELEGRAM_ALLOWED_USERS` through an outer middleware; anyone else is only told their ID.
  - A plain-text message is treated as a topic and answered with mode and source buttons.
  - Each research runs in a worker thread with its own event loop (`asyncio.to_thread` + `asyncio.run`), so the bot stays responsive. Progress is passed as a plain string that a ticker task renders into the status message every few seconds.
  - Replying to any message that contains `#N`, or using `/ask N`, answers from report N's text (`my_browser/ask.py`).
- Modes: `quick` = `research_report`; `deep` = gpt-researcher's recursive `deep` report (`DEEP_RESEARCH_*` keys); `detailed` = `my_browser/detailed.py`, a port of upstream `backend/report_type/detailed_report`, which is **not shipped in the pip package**. It runs one broad research pass, then each subtopic is researched and written as its own section (each at least `TOTAL_WORDS` long), then the intro, TOC, conclusion and references are stitched together.
- `my_browser/prompts.py` makes search queries English-first while the report stays in `LANGUAGE`. It registers the `english_first` prompt family (`PROMPT_FAMILY` in `config.json`) and monkeypatches two upstream spots:
  - `query_processing.generate_sub_queries`: upstream `plan_research_outline()` doesn't pass `prompt_family`, so a custom family never reached sub-query generation.
  - `DeepResearchSkill.generate_search_queries`: its query prompt is inline, so it gets the language rule and today's date.
  - `pipeline.research()` imports the module for these side effects.
- `my_browser/export.py`: DOCX is rendered in-process (mistune → htmldocx). The PDF is rendered by WeasyPrint **in a child process** with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`, because WeasyPrint needs Homebrew's pango (`brew install pango`) and dyld reads that variable only at process start.

## Deployment

- GitHub: public repo `MrBoomer1981/DEEper`. The pre-2026-10 DEEper code (aiogram + Groq + Tavily) lives in the `legacy` branch. Old history contains a leaked `.env`.
- Railway deploys `main` with the `Dockerfile` (`railway.json`, a single replica, because two Telegram pollers conflict).
- There is no SearXNG on Railway. `SOURCES["web"]` becomes `tavily` automatically when `TAVILY_API_KEY` is set and `SEARX_URL` is not; `WEB_RETRIEVER` overrides this.
- The bot also accepts `TELEGRAM_TOKEN` (the DEEper v1 name) as a fallback for `TELEGRAM_BOT_TOKEN`.
- Reports persist only if a volume is mounted at `/app/reports`.
- The container runs in UTC, and on Linux WeasyPrint finds pango without the DYLD workaround.
- Build the image locally with `docker build -t my-browser-bot .` (about 6 min, ~1.9 GB). To test it against the host's SearXNG and mock LLM, use `host.docker.internal`.

## Non-obvious behavior

- DeepSeek through gpt-researcher's `deepseek` provider (ChatOpenAI pointed at api.deepseek.com):
  - Thinking mode is disabled via `LLM_KWARGS.extra_body.thinking`; otherwise every call reasons, which makes it slower and more expensive.
  - langchain-openai sends `max_completion_tokens`, but DeepSeek reads only `max_tokens` (default 8K in non-thinking mode). That is why `max_tokens` is duplicated inside `extra_body`.
- Embeddings are intentionally not configured. With `CONTEXT_FILTER=keyword` and no `OPENAI_API_KEY`, gpt-researcher falls back to keyword ranking. If `OPENAI_API_KEY` is present in the environment, it silently switches to OpenAI embeddings. The keyword filter (`gpt_researcher/context/lexical.py`) only strips English plurals and drops only English stopwords, so Russian word forms count as different terms.
- Report-writing calls use `stream=True`. An OpenAI-compatible endpoint or mock without SSE streaming gets each call retried 10× with backoff, which looks like a hang.
- SearXNG (`searxng/settings.yml`) must keep the `json` format enabled. From this machine, only `google cse` and `yahoo` answer relevantly. bing returns off-topic junk for Cyrillic queries (Nickelodeon, Halloween stores, banks), and those pages also slow scraping. google, yandex, qwant, duckduckgo, brave and wikipedia return captcha or rate-limit errors. The `semantic_scholar` retriever gets 429 without a key and has no key support, so it is excluded from `--sources`.
- The references section lists every visited URL (upstream behavior), including pages later filtered out as irrelevant.
- `GPTResearcher.get_costs()` prices unknown models at OpenAI defaults, so it is wrong for DeepSeek. Real spend is on the DeepSeek dashboard.

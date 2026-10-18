# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

Personal deep-research engine that writes long reports: the `gpt-researcher` pip library for orchestration, a local SearXNG for free web search, and DeepSeek (`deepseek-flash`) as the LLM. The user writes in Russian; reports default to Russian.

## Commands

```bash
docker compose up -d                       # start local SearXNG on 127.0.0.1:8080 (needs SEARXNG_SECRET in .env)
uv run research.py "тема"                  # detailed report (default mode) -> reports/
uv run research.py "тема" --mode deep --sources all --words 2500
uv run research.py --help
uv add <pkg>                               # dependencies are managed by uv (Python 3.12)
```

Outputs land in `reports/` as `<date>_<slug>.{md,docx,pdf,json}`; the `.json` holds query, mode, duration, word count and source URLs. There are no tests yet.

## Architecture

- `research.py`: CLI. Loads `.env`, maps flags to env vars, runs `preflight()` (a 1-token DeepSeek call plus a SearXNG ping, so a bad key, empty balance or a stopped container fails in seconds), then runs one of three modes and saves the outputs.
- Configuration precedence in gpt-researcher: **env vars > `config.json` > library defaults**. CLI flags work by setting env vars (`RETRIEVER`, `TOTAL_WORDS`, `LANGUAGE`), and any key in `config.json` can be overridden from `.env`.
- Modes: `quick` = `research_report`; `deep` = gpt-researcher's recursive `deep` report (`DEEP_RESEARCH_*` keys); `detailed` = `my_browser/detailed.py`, a port of upstream `backend/report_type/detailed_report`, which is **not shipped in the pip package**. It runs one broad research pass, then each subtopic is researched and written as its own section (each at least `TOTAL_WORDS` long), then the intro, TOC, conclusion and references are stitched together.
- `my_browser/export.py`: DOCX is rendered in-process (mistune → htmldocx). The PDF is rendered by WeasyPrint **in a child process** with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`, because WeasyPrint needs Homebrew's pango (`brew install pango`) and dyld reads that variable only at process start.

## Non-obvious behavior

- DeepSeek through gpt-researcher's `deepseek` provider (ChatOpenAI pointed at api.deepseek.com):
  - Thinking mode is disabled via `LLM_KWARGS.extra_body.thinking`; otherwise every call reasons, which makes it slower and more expensive.
  - langchain-openai sends `max_completion_tokens`, but DeepSeek reads only `max_tokens` (default 8K in non-thinking mode). That is why `max_tokens` is duplicated inside `extra_body`.
- Embeddings are intentionally not configured. With `CONTEXT_FILTER=keyword` and no `OPENAI_API_KEY`, gpt-researcher falls back to keyword ranking. If `OPENAI_API_KEY` is present in the environment, it silently switches to OpenAI embeddings.
- Report-writing calls use `stream=True`. An OpenAI-compatible endpoint or mock without SSE streaming gets each call retried 10× with backoff, which looks like a hang.
- SearXNG (`searxng/settings.yml`) must keep the `json` format enabled. From this machine, only `google cse` and `yahoo` answer relevantly. bing returns off-topic junk for Cyrillic queries (Nickelodeon, Halloween stores, banks), and those pages also slow scraping. google, yandex, qwant, duckduckgo, brave and wikipedia return captcha or rate-limit errors. The `semantic_scholar` retriever gets 429 without a key and has no key support, so it is excluded from `--sources`.
- The references section lists every visited URL (upstream behavior), including pages later filtered out as irrelevant.
- `GPTResearcher.get_costs()` prices unknown models at OpenAI defaults, so it is wrong for DeepSeek. Real spend is on the DeepSeek dashboard.

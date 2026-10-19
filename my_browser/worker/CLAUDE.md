# CLAUDE.md: worker

Guidance for changing `my_browser/worker/`: what the other parts expect from the worker, and what it expects from them and from gpt-researcher. What the worker does is in the root CLAUDE.md; paths below are relative to `my_browser/` unless they are repo-root files.

## What others rely on

**Core** calls the worker's API (`api.py`; in-process unless `WORKER_URL` is set):
- `POST /ask {markdown, question}` → `{a, cost_rub, research?}`. `research` is the topic from the model's last «ИССЛЕДОВАТЬ: …» line, already cut from `a`; core stores the item in `.qa.json` and both UIs offer to run it.
- `POST /followups {query, markdown}` → `{topics, cost_rub}`; a failure gives `topics: []`, never an error.
- `POST /clarify {query}` → `{questions: [{q, options}], cost_rub}`. The options never include `ANY_ANSWER`; the UIs add it.
- `POST /refine {query, answers: [[question, answer]]}` → `{topic, cost_rub}`; the original topic comes back when the rewrite looks wrong.
- In the all-in-one app these handlers run on NiceGUI's event loop, so blocking work inside them (sync HTTP, heavy CPU) freezes the web UI.

**Core** also takes what the job loop hands in (`pipeline.render()`):
- Files keyed by suffix: `.md` and `.json` always, `.docx` and `.pdf` only when rendering worked.
- The JSON fields:
  - `query`, `mode`, `retriever`, `created`, `duration_sec`, `words`;
  - `writer`: the model name without the provider;
  - `cost`: `Meter.summary()`, whose `rub` core and both UIs show;
  - `sources`: a list of URLs, whose length is the library's source count; the sources tab lists them;
  - `followups`: `[{kind, query, why, mode}]`;
  - `factcheck` (only when the job had it): `factcheck.summary()`, i.e. counts by verdict, `accuracy`, `relinked`, `rewritten`, `fixed`, and `doubtful` items `{statement, url, verdict, note, raw}`, where `raw` is the sentence as it stands in the report and core puts its «?» after it; plus `cost_rub` and `seconds`, or `error` when the check failed;
  - `parent`: the parent report's file name without suffix.
  - Readers: `core/library.py`, `core/service.py`, `core/web.py`, the bot (through core) and `eval/`.
- The Markdown ends with `factcheck.section()` («Проверка фактов», only when checked) and then `common.report.section(followups)`; `common.report.body()` strips both by their titles. The bot's `_conclusion()` looks for a level-2 «Заключение», «Выводы» or «Conclusion» heading. `suggest._excerpt()` looks for conclusion, limitation and reference headings.
- Progress: `Progress.snapshot()` matches core's `ProgressRequest`; the job page shows `urls` as `[url, score]` pairs.

**In-process users** (no API):
- `research.py` and `eval/run.py` use `pipeline` (`run_settings`, `config_file`, `preflight`, `research` and its `Research(markdown, sources, pages)`, `render`), `factcheck.run` and `hooks.blocked`.
  - `research.py` also uses `suggest.followups`.
  - eval also uses `hooks.SEARCH_META` (titles for its off-topic judge), `suggest.fast` and `suggest.json_list` (its judges), and for its citation judge `factcheck.claims`, `Passages`, `translate`, plus `LINK`, `text_and_references` and `units` in `eval/metrics.py`. A change there changes eval's «Точность цитат»: bump `JUDGE` in `eval/run.py` and rejudge the last run (`--rejudge`) so the numbers stay comparable.
- Two log lines are parsed:
  - `Added source url to research (s/3): url`, written by `hooks.py` through gpt-researcher's `stream_output`, is parsed by `runner.SOURCE_LINE` and `eval/metrics.ADDED`;
  - upstream's `based on the following queries: [...]` is parsed by `runner.QUERIES_LINE`.
- `app.py` and `worker/__main__.py` start `runner.run_forever()` in a thread.

## What the worker relies on

- **Core's queue endpoints** (`/api/worker/*`, see `core/CLAUDE.md`) at `CORE_URL`. Core assumes a single worker: `WORKER_ID` is new on every start, and its hello gives up whatever another id was running.
- **Search** (`hooks.py`; shapes in `search/CLAUDE.md`):
  - `/select` is called once per batch of search results, with a 120 s timeout. Its `usage` is merged into the job's meter.
  - `/read` is called for every page, with a 180 s timeout.
  - Every page `/read` returns with text is also kept in `hooks.read_pages` for the fact-check: a fresh dict per `research()` call, shared by the tasks gpt-researcher starts from it.
  - When search fails, a `/read` error costs only that page (an empty result). A `/select` error costs that sub-query's sources: gpt-researcher logs `Error processing sub-query` and goes on.
  - `preflight()` does not check the search part. With `SEARCH_URL` set and search down, the report gets written without sources.
- **gpt-researcher 0.16.1** (locked in `uv.lock`; `pyproject.toml` allows newer versions): the worker patches its internals at import. After an upgrade, re-check:
  - `hooks.py`:
    - retriever classes named `*Search` with a synchronous `search()` that returns dicts with `href`/`url`, `title` and `body`;
    - `ResearchConductor._get_new_urls(self, url_set_input)`, and `self.researcher.visited_urls`, `query`, `parent_query`, `verbose`, `websocket`;
    - `Scraper.extract_data_from_url(self, link, session)`, returning `{url, raw_content, image_urls, title}`;
    - `stream_output`.
  - `prompts.py`:
    - the literal "You must respond with a list of strings" in `PromptFamily.generate_search_queries_prompt`. The English-first rule is inserted before it; if upstream rewords it, the rule silently disappears.
    - `prompt_family_mapping`, `get_prompt_family`, `query_processing.generate_sub_queries(query, parent_query, report_type, context, cfg, cost_callback, **kwargs)`, `DeepResearchSkill.generate_search_queries(self, query, num_queries)`.
  - `stemming.py`: `tokenize`, `_TOKEN`, `_STOPWORDS`, `_stem` in `gpt_researcher/context/lexical.py`.
  - `common/costs.py`: `GenericLLMProvider.get_chat_response(self, messages, stream, websocket=None, **kwargs)`, `last_usage_metadata`, `last_response_metadata`.
  - `runner.py`:
    - the `scraper` logger, whose console handler `gpt_researcher.actions` creates; it is imported first;
    - the wording of the `based on the following queries:` message in `skills/researcher.py`.
  - `detailed.py` is a port of upstream's `backend/report_type/detailed_report`.
    - It uses `GPTResearcher`'s `conduct_research`, `get_subtopics`, `write_introduction`, `write_report_conclusion`, `get_draft_section_titles`, `get_similar_written_contents_by_draft_section_titles`, `write_report`, `extract_headers`, `extract_sections`, `table_of_contents`, `context`, `visited_urls`, `agent`, `role` and `cfg`, and the constructor's `report_type="subtopic_report"`, `parent_query`, `visited_urls`, `agent` and `role`.
    - Compare it with upstream's current version when upgrading.
- **The fact-check** (`factcheck.py`) runs after `research()` with the run's config file: FAST_LLM judges, the run's `SMART_LLM` (the writer) corrects. It needs `config_path` still alive, so callers run it inside `config_file()`. It changes only sentences it can back with a page the research read; the prompts' wording («a conclusion drawn from what the page says counts», «correct only what differs») is what keeps it from cutting the report's reasoning, which an earlier version did.
- **Configuration**:
  - `run_settings()` reads `config.json` per research and adds the run's choices.
  - Environment variables beat it inside gpt-researcher, so an env `SMART_LLM` overrides the writer toggle (that is how the mock works).
  - Outside a research, `ask.py` (`SMART_LLM`) and `suggest.py` (`FAST_LLM`) read `config.json` plus the environment through `Config(CONFIG_PATH)`. The writer toggle does not reach Q&A.
- **ContextVars**:
  - The job loop sets `costs.current` (the meter) and `hooks.blocked` before `asyncio.run()`, which copies them into the research.
  - Work moved to another thread (`run_in_executor`, a new thread) loses them unless it copies the context. Its costs then go uncounted and the blocklist stops applying.
- **Import order**:
  - `hooks.py` imports `common.gptr` before it replaces `Scraper.extract_data_from_url`, so search keeps the original in the same process.
  - `pipeline.research()` imports `common.costs`, `hooks`, `prompts` and `stemming` for their patches. Code that runs gpt-researcher without `research()` must import them itself.
- **Export**:
  - The PDF is rendered by `python -m my_browser.worker.export` in a child process (`cwd=ROOT`), so that module path is part of the contract.
  - It needs pango (Homebrew on macOS through `DYLD_FALLBACK_LIBRARY_PATH`, apt packages in the `Dockerfile`) and the fonts in `static/fonts`.
  - A failed DOCX or PDF is skipped, not fatal.
- **Packages that come with gpt-researcher** and are not declared in the `worker` group: mistune, htmldocx, python-docx and weasyprint (export), nltk (stemming), json-repair (suggest), requests (preflight). If an upgrade drops one, add it to the group.
- **Services**:
  - DeepSeek (`DEEPSEEK_API_KEY`). `config.json` disables thinking and repeats `max_tokens` in `extra_body`.
  - SearXNG (`SEARX_URL`) or Tavily (`TAVILY_API_KEY`), plus arXiv and OpenAlex.
  - The CBR exchange rate (`USD_RUB` overrides it).

## When you change…

| Change | Also update |
|---|---|
| a field of the report JSON | `core/library.py` (`add`), `core/service.py`, `core/web.py`, the bot, `eval/metrics.py` and `eval/run.py`; reports already on the volume keep the old shape |
| the shape or kinds of follow-up topics | `common/report.py` (`KINDS`, `section`), the cards in `core/web.py`, the bot's buttons |
| the `Added source url to research` line | `runner.SOURCE_LINE`, `eval/metrics.ADDED` |
| report headings (conclusion, limitations, references) | the bot's `_conclusion()`, the patterns in `suggest.py`, `factcheck.REFERENCES` (eval uses it too) |
| the fact-check's summary or section | `core/web.py` (the «факты» chip, `mark_doubtful()`, the block), the bot's `_factcheck_line()`, `common/report.VERDICT_TEXT`, eval's `fc_*` columns |
| how `factcheck.claims()` cuts sentences | `raw` must still match the report text for core's marks; eval's sampled pairs change, so bump `JUDGE` |
| a request or response of `api.py` | `core/service.py`, and `core/qa.py` for `/ask` |
| the progress payload | `ProgressRequest` in `core/api.py`, `core/queue.progress`, the job page in `core/web.py` |
| a mode, source or writer | the table in `core/CLAUDE.md` |
| how long a mode takes | `core/web.MODE_HINTS`, the bot's `HELP` |

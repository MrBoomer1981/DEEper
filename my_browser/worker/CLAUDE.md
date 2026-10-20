# CLAUDE.md: worker

The worker is DEEper's research engine: gpt-researcher with our patches, the job loop, the fact-check, the cheap LLM helpers and the DOCX/PDF export. This file covers what it does, how to run and check it alone, what the other parts expect from it, and what it expects from them and from gpt-researcher. The repository's rules for working in one part are in the root CLAUDE.md. Paths below are relative to `my_browser/` unless they are repo-root files.

## What the worker does

- `worker/runner.py`: the job loop.
  - One research at a time, in a plain thread.
  - Per job it sets the cost meter and the blocklist ContextVars (`asyncio.run` copies them into the research), runs `pipeline.research()`, asks for follow-ups, renders the files and hands them in (with retries, since core may be restarting).
  - A handler on gpt-researcher's `scraper` logger collects the planned queries and the `Added source url (score)` lines.
  - A ticker thread sends progress every few seconds when it changed and every 30 s as a heartbeat.
- `worker/pipeline.py`:
  - `run_settings()` builds config.json plus the run's sources, writer, words and language, and `config_file()` writes it to a temporary file for each run, so nothing process-wide changes during a research.
  - Configuration precedence in gpt-researcher is **env vars > config file > library defaults**. Any key from `config.json` can still be overridden in `.env`, and an environment variable beats the run's choice too.
  - `preflight()`: a 1-token DeepSeek call and a SearXNG JSON ping, so a bad key, an empty balance or a stopped container fails in seconds. It raises `PreflightError`.
  - `research()`: one of three modes. It returns `Research(markdown, sources, pages)`, where `pages` holds the text of every page read.
  - `render()`: the md/json/docx/pdf bytes, which core stores.
- Modes:
  - `quick` is gpt-researcher's `research_report`.
  - `deep` is its recursive `deep` report (`DEEP_RESEARCH_*` keys).
  - `detailed` is `worker/detailed.py`, a port of upstream `backend/report_type/detailed_report`, which is **not shipped in the pip package**:
    - One broad research pass first. Then all subtopics are researched at once while the introduction is written (since 20.10.2026; the sub-researchers share one `visited_urls` set).
    - Their sections, each at least `TOTAL_WORDS` long, are written strictly one after another, so that each sees the ones already written. Then the TOC, conclusion and references are stitched together.
    - The subtopic list comes from the writer model capped at 2,000 tokens. The draft section titles, used only to find what earlier sections wrote, come from FAST_LLM capped at 1,000. `_limited()` puts the caps in `extra_body.max_tokens`, the only cap DeepSeek reads.
    - With Pro writing it takes about 8–10 minutes, about 6 with Flash (20.10.2026).
    - Its `## Источники` list is built from the links cited inline, not from every visited URL. If fewer than 3 links are cited (APA author-date style), it falls back to all visited URLs.
- `worker/hooks.py` patches gpt-researcher in the worker process:
  - Every `*Search` retriever's `search()` remembers titles and snippets in `SEARCH_META`.
  - `ResearchConductor._get_new_urls` is where search results become research sources, and later the references. It asks search's `/select`, passing candidates with titles and snippets, per-domain counts of the research so far (`visited_urls`, which detailed sub-researchers share) and the blocklist. The rating's usage is merged into the job's meter. URLs are re-checked against `visited_urls` after the await, because parallel sub-queries race.
  - `Scraper.extract_data_from_url` goes through search's `/read` and keeps every page's text in `read_pages`, a dict per research set by `research()`, for the fact-check.
  - Searches of arXiv and SearXNG start at most every 3 s per process (`PACE`). arXiv's API asks for that, and its retriever opens a new client per search, so parallel sub-queries got HTTP 429. SearXNG's Google CSE and Yahoo time out or suspend themselves («unusual traffic») under the detailed report's parallel bursts. Tavily is not paced.
  - arXiv gets no Cyrillic queries (`ENGLISH_ONLY`): gpt-researcher always also searches the research's own Russian query, and arXiv took 150 s to answer one, with nothing useful.
  - `SOURCE_FILTER=off` disables the selection.
  - The search engines themselves still run inside gpt-researcher here.
- `worker/prompts.py` registers the `deeper` prompt family (`PROMPT_FAMILY` in `config.json`), which does two things:
  - It makes search queries English-first while the report stays in `LANGUAGE`.
  - It gives the writer its citation rules (`CITATION_RULES`, plan 3.13) in all five writing prompts: the quick report, the detailed report's sections, introduction and conclusion, and the deep report.
    - A source is cited only for what it says itself; the report's own comparisons, generalizations and forecasts go without a citation.
    - A fact and its interpretation go in separate sentences, with the citation right after its own sentence rather than at the end of the paragraph.
    - Specific facts come from the research, never from the model's memory.
    - The conclusion is written from the report itself, so its rules (`CONCLUSION_RULES`) keep the report's citations for restated facts and leave the conclusions uncited.
    - Upstream asked for a citation on every substantive claim, at the end of the sentence or paragraph; the fact-check then found conclusions cited «for show». Those upstream lines are cut by their wording and the rules appended, so if an upgrade rewords them the rules still apply.
  - It also monkeypatches three upstream spots:
    - `query_processing.generate_sub_queries`: upstream `plan_research_outline()` doesn't pass `prompt_family`, so a custom family never reached sub-query generation.
    - `skills.writer.generate_report`: upstream `ReportGenerator.write_report()` doesn't pass it either, so until 20.10.2026 the report text itself (quick, the detailed report's sections, deep) was written with upstream's prompts; only the introduction and conclusion got the family.
    - `DeepResearchSkill.generate_search_queries`: its query prompt is inline, so it gets the language rule and today's date.
- `worker/stemming.py` swaps gpt-researcher's BM25 tokenizer (`context/lexical.py`) for one with the Snowball Russian stemmer (from nltk) and Russian stopwords; Latin tokens go through upstream's stemmer unchanged. `pipeline.research()` imports `hooks`, `prompts` and `stemming` for their side effects.
- `worker/factcheck.py` checks a written report against the pages its research read, when the job has `factcheck`:
  - `claims()`: every sentence with a link, paired with each URL it links, plus the sentence before it as context (what «их» refers to). When the sentence cites several sources, it also keeps this link's anchor (`cited_as`): the judge checks only the part that citation stands for.
    - A single citation standing after the period counts for the sentence before it.
    - A bare list of sources («Источники: [A](…), [B](…)») counts for nothing.
    - Links are found by `common.text.MARKDOWN_LINK`, which keeps URLs with parentheses whole.
  - `Passages`: the read pages in 120-word chunks. A claim's chunks are found by BM25 (the Russian-aware tokenizer) over the claim and its English translation (`translate()`, one FAST_LLM call per 30 claims), plus shared numbers normalized across languages (`1 500` = `1,500`).
  - FAST_LLM judges the claims of one page in one call over the chunks they need together (≤8 claims, ≤10 chunks). Verdicts:
    - SUPPORTED: a conclusion drawn from what the page says counts;
    - PARTIAL;
    - MISMATCH: another number, date, name or fact;
    - CONTRADICTED, NOT_FOUND, NO_TEXT;
    - UNREAD: the research did not read the cited page.
  - Fixes:
    - A claim its page does not support, but another read page does, gets that link (or loses the extra link when the sentence already cites that page).
    - MISMATCH and CONTRADICTED sentences are corrected by the writer model (`SMART_LLM` of the run) from the page's chunks. The correction is dropped if it changes the links, the length too much, or nothing.
    - PARTIAL is only counted: several sources often each support a part of one sentence.
  - The summary (counts, `fixed`, `doubtful` with the sentence as `raw` for core's marks, `cost_rub`) goes into the report's JSON as `factcheck`, and `section()` becomes the «Проверка фактов» section before «Что исследовать дальше». Its cost is metered apart and then added to the research's. Any failure leaves the report unchecked, never unsaved.
  - While judging it reports «Проверяю факты: k из n» (`phases.checking`), so the job page's percent moves through the check.
  - About 0.6–0.9 ₽ for a quick report off-peak, mostly the chunks sent to the judge.
- `worker/suggest.py` holds the cheap FAST_LLM suggestions:
  - Follow-ups («Что исследовать дальше»): 5 topics, deeper/adjacent/verify, each with `why` and a recommended mode, from the report's outline, conclusions and limitation sections. A failure returns `[]` and never blocks saving.
  - Clarification («Уточнить»): `questions()`, `refine()`.
  - `render()` puts follow-ups into the JSON and as the last `## Что исследовать дальше` section into the documents. The web view strips the appended sections (`common/report.body()`) and renders cards and its own fact-check block instead. Older reports get topics on demand («Предложить темы»), in the JSON only.
- `worker/ask.py` answers a question from a report's text with `SMART_LLM`; `worker/api.py` exposes `/ask`, `/followups`, `/clarify`, `/refine` for core.
- `worker/export.py`: DOCX is rendered in-process (mistune → htmldocx). The PDF is rendered by WeasyPrint **in a child process** (`python -m my_browser.worker.export`) with `DYLD_FALLBACK_LIBRARY_PATH=/opt/homebrew/lib`, because WeasyPrint needs Homebrew's pango (`brew install pango`) and dyld reads that variable only at process start.
- Writer: `SMART_LLM` (report text and Q&A) defaults to `deepseek-v4-pro`; FAST and STRATEGIC stay on `deepseek-flash`. `--writer`, the bot default and the web toggle pick `WRITERS[...]` through `run_settings(writer=...)`.
- What a research costs is metered by `common/costs.py` (see `common/CLAUDE.md`). URLs go through `common/text.readable_url` before the writer sees them.

## Run and check it alone

```bash
uv run python -m my_browser.worker                          # API at 127.0.0.1:8502 (WORKER_PORT) + the job loop, taking jobs from core at CORE_URL
uv run ../../research.py "тема" --mode quick --writer flash   # one research without core or servers -> reports/ (or REPORTS_DIR)
```

- **`research.py`** runs `pipeline.research()`, the fact-check (`--factcheck`), follow-ups and rendering in one process, with search in-process.
  - With the mock LLM's environment (`dev/mock_llm.py`'s docstring) it costs nothing.
  - Real runs cost money: a quick Flash report about 1 ₽, a detailed Pro report about 12 ₽ off-peak. DeepSeek charges twice as much at peak, 01–04 and 06–10 UTC on weekdays.
- **The job loop.** Use the all-in-one app (the `check-project` skill), or core and the worker apart: the worker with `CORE_URL`, core with `WORKER_URL`.
- **The API alone**, with the service token of the same `REPORTS_DIR`: `curl -s localhost:8502/clarify -H "Authorization: Bearer $T" -H 'Content-Type: application/json' -d '{"query": "тема"}'`.
- **A phase.** After adding or changing one, feed the line through `percent()` and `status()` of `core/ui/progress.py`. With the mock, a run is too fast to show most phases on the job page.
- **Quality.** Prompts, the fact-check, the detailed report and source handling in `hooks.py` are judged by numbers: `eval/run.py` (see `eval/CLAUDE.md`), against the last run made the same way.
- **The whole project.** Before committing, run the `check-project` skill.

## What others rely on

**Core** calls the worker's API (`api.py`; in-process unless `WORKER_URL` is set):
- `POST /ask {markdown, question}` → `{a, cost_rub, research?}`. `research` is the topic from the model's last «ИССЛЕДОВАТЬ: …» line, already cut from `a`; core stores the item in `.qa.json` and both UIs offer to run it.
- `POST /followups {query, markdown}` → `{topics, cost_rub}`; a failure gives `topics: []`, never an error.
- `POST /clarify {query}` → `{questions: [{q, options}], cost_rub}`. The options never include `ANY_ANSWER`; the UIs add it.
- `POST /refine {query, answers: [[question, answer]]}` → `{topic, cost_rub}`; the original topic comes back when the rewrite looks wrong.
- `GET /health` → `{ok: true}`.
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
  - Readers: `core/library.py`, `core/service.py`, the screens in `core/ui/` (report, library, radar), the bot (through core) and `eval/`.
- The Markdown ends with `factcheck.section()` («Проверка фактов», only when checked) and then `common.report.section(followups)`; `common.report.body()` strips both by their titles. The bot's `_conclusion()` looks for a level-2 «Заключение», «Выводы» or «Conclusion» heading. `suggest._excerpt()` looks for conclusion, limitation and reference headings.
- Progress: `Progress.snapshot()` matches core's `ProgressRequest`; the job page shows `urls` as `[url, score]` pairs. Every phase line comes from `common/phases.py`: core's job page parses it there, the bot shows it as it is.

**In-process users** (no API):
- `research.py` and `eval/run.py` use `pipeline` (`run_settings`, `config_file`, `preflight`, `research` and its `Research(markdown, sources, pages)`, `render`), `factcheck.run` and `hooks.blocked`.
  - `research.py` also uses `suggest.followups`, and prints every phase of the fact-check.
  - eval also uses `hooks.SEARCH_META` (titles for its off-topic judge), `suggest.fast` and `suggest.json_list` (its judges), and for its citation judge `factcheck.claims`, `Passages`, `translate`, plus `LINK`, `text_and_references` and `units` in `eval/metrics.py`. A change there changes eval's «Точность цитат»: bump `JUDGE` in `eval/run.py` and rejudge the last run (`--rejudge`) so the numbers stay comparable.
- Two log lines are parsed:
  - `Added source url to research (s/3): url`, written by `hooks.py` through gpt-researcher's `stream_output`, is parsed by `runner.SOURCE_LINE` and `eval/metrics.ADDED`;
  - upstream's `based on the following queries: [...]` is parsed by `runner.QUERIES_LINE`.
- `app.py` and `worker/__main__.py` start `runner.run_forever()` in a thread.
- `dev/mock_llm.py` recognizes the worker's prompts (follow-ups, clarification, Q&A, the fact-check's translation, judge and correction) by phrases. A reworded prompt gets the mock's generic answer, so free runs of that step stop meaning much until the mock learns the new phrase.

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
    - the retriever class names `ArxivSearch` and `SearxSearch` (keys of `PACE` and `ENGLISH_ONLY`), each retriever's `query` attribute, and that `search()` runs in a worker thread (`asyncio.to_thread`): pacing sleeps there;
    - retriever classes named `*Search` with a synchronous `search()` that returns dicts with `href`/`url`, `title` and `body`;
    - `ResearchConductor._get_new_urls(self, url_set_input)`, and `self.researcher.visited_urls`, `query`, `parent_query`, `verbose`, `websocket`;
    - `Scraper.extract_data_from_url(self, link, session)`, returning `{url, raw_content, image_urls, title}`;
    - `stream_output`.
  - `prompts.py`:
    - the literal "You must respond with a list of strings" in `PromptFamily.generate_search_queries_prompt`. The English-first rule is inserted before it; if upstream rewords it, the rule silently disappears.
    - the five writing prompts that get `CITATION_RULES` (`generate_report_prompt`, `generate_subtopic_report_prompt`, `generate_report_introduction`, `generate_deep_research_prompt`; `generate_report_conclusion` gets `CONCLUSION_RULES`), and upstream's citation lines that `EVERY_CLAIM` and `PLACEMENT` cut out. If upstream rewords those lines, the rules are still appended, but its old instruction (a citation on every claim, at the end of the sentence or paragraph) stays beside them. A new writing prompt upstream starts without our rules.
    - `prompt_family_mapping`, `get_prompt_family`, `query_processing.generate_sub_queries(query, parent_query, report_type, context, cfg, cost_callback, **kwargs)`, `DeepResearchSkill.generate_search_queries(self, query, num_queries)`.
    - `generate_report` as `skills/writer.py` imports it by name and calls it with keyword arguments including `cfg`; the patch adds the configured `prompt_family`, which `ReportGenerator.write_report()` leaves out. If upstream starts passing it, `setdefault` keeps upstream's; if the call moves elsewhere, the report is written with upstream's prompts again, which shows as citations at the end of paragraphs and the fact-check's not-found count going up.
  - `stemming.py`: `tokenize`, `_TOKEN`, `_STOPWORDS`, `_stem` in `gpt_researcher/context/lexical.py`.
  - `common/costs.py`: `GenericLLMProvider.get_chat_response(self, messages, stream, websocket=None, **kwargs)`, `last_usage_metadata`, `last_response_metadata`.
  - `runner.py`:
    - the `scraper` logger, whose console handler `gpt_researcher.actions` creates; it is imported first;
    - the wording of the `based on the following queries:` message in `skills/researcher.py`.
  - `detailed.py` is a port of upstream's `backend/report_type/detailed_report`.
    - It uses `GPTResearcher`'s `conduct_research`, `write_introduction`, `write_report_conclusion`, `get_similar_written_contents_by_draft_section_titles`, `write_report`, `extract_headers`, `extract_sections`, `table_of_contents`, `query`, `context`, `subtopics`, `visited_urls`, `agent`, `role`, `prompt_family`, `add_costs` and `cfg`, and the constructor's `report_type="subtopic_report"`, `parent_query`, `visited_urls` (kept as the same set object, which the parallel sub-researchers rely on), `agent` and `role`.
    - It calls `actions.generate_draft_section_titles` and `utils.llm.construct_subtopics` itself with a copy of the config (`_limited()`): they read `smart_llm_model`, `smart_llm_provider`, `smart_token_limit` and `llm_kwargs`, and `_limited()` sets those from `fast_llm_*` and the cap. If upstream changes which fields they read, the cap or the switch to Flash silently stops.
    - `conduct_research()` replaces the researcher's context with what it found, which is why a sub-researcher needs no context from the others.
    - Compare it with upstream's current version when upgrading.
  - gpt-researcher is in the `search` group too: an upgrade changes both parts, so run the `check-project` skill and recheck `search/CLAUDE.md`'s list of upstream pieces.
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
| a field of the report JSON | `core/library.py` (`add`), `core/service.py`, `core/ui/` (`report.py`, `radar.py`), the bot, `eval/metrics.py` and `eval/run.py`; reports already on the volume keep the old shape |
| the shape or kinds of follow-up topics | `common/report.py` (`KINDS`, `section`), the end of `core/ui/report.py`, the feed in `core/ui/radar.py`, the bot's buttons |
| the `Added source url to research` line | `runner.SOURCE_LINE`, `eval/metrics.ADDED` |
| report headings (conclusion, limitations, references) | the bot's `_conclusion()`, the patterns in `suggest.py`, `factcheck.REFERENCES` (eval uses it too) |
| the fact-check's summary or section | `core/ui/report.py` (`mark_doubtful()`, the «⋯» row, the block), the bot's `_factcheck_line()`, `common/report.VERDICT_TEXT`, eval's `fc_*` columns |
| how `factcheck.claims()` cuts sentences | `raw` must still match the report text for core's marks; eval's sampled pairs change, so bump `JUDGE` |
| the writer's citation rules (`prompts.CITATION_RULES`) | the fact-check checks only sentences with a link, so the rules decide what it sees; measure on a detailed Pro report (`eval/run.py --mode detailed --writer pro --factcheck`), where the fact-check's not-found and partial counts show the effect over all claims |
| a prompt's wording | `dev/mock_llm.py` if it recognizes that prompt by the phrase you changed |
| a request or response of `api.py` | `core/service.py`, and `core/qa.py` for `/ask` |
| a phase (the progress line) | write it in `common/phases.py`, never as a literal here; a new phase or counter needs its pattern there and a branch in `core/ui/progress.py` (`percent()`, `status()`); the bot shows the line as it is |
| the progress payload | `ProgressRequest` in `core/api.py`, `core/jobs.progress`, `core/ui/progress.py` (the percent) and `core/ui/job.py` |
| a mode, source or writer | the table in `core/CLAUDE.md` |
| how long a mode takes | `core/ui/kit.MODE_HINTS`, the bot's `HELP` |

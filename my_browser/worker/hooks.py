"""Where gpt-researcher meets the search part.

Importing this module patches gpt-researcher in the worker process:
- every retriever's search() remembers titles and snippets of its results (SEARCH_META), so the
  search part can rate candidates by more than their URL; arXiv and SearXNG wait for their turn (PACE),
  and arXiv gets no Cyrillic queries (ENGLISH_ONLY);
- ResearchConductor._get_new_urls (where search results become research sources, and later the
  references) asks the search part which candidates to take (POST /select);
- Scraper.extract_data_from_url reads every page through the search part (POST /read), which adds
  free fallbacks to the plain fetch, and keeps what it read in read_pages for the fact-check.
The search engines themselves still run here, inside gpt-researcher.
"""

import contextvars
import inspect
import logging
import os
import re
import threading
import time
from collections import Counter

import gpt_researcher.retrievers as retrievers
from gpt_researcher.actions import stream_output
from gpt_researcher.scraper.scraper import Scraper
from gpt_researcher.skills.researcher import ResearchConductor

from my_browser.common import costs
from my_browser.common import gptr  # noqa: F401  keeps the unpatched Scraper method for the search part
from my_browser.common.clients import call
from my_browser.common.text import host, readable_url

logger = logging.getLogger(__name__)

SEARCH_META: dict[str, dict] = {}  # url -> title/snippet from the last searches that returned it
blocked: contextvars.ContextVar[list[str]] = contextvars.ContextVar("blocked_domains", default=[])  # the user's blocklist, per research
# url -> {"text", "title"} of every page read, per research; pipeline.research() sets a fresh dict
read_pages: contextvars.ContextVar[dict | None] = contextvars.ContextVar("read_pages", default=None)

# Seconds between searches of one engine, for this process. The detailed report researches its subtopics in
# parallel, and a burst of searches at once breaks two engines:
# - arXiv asks for at most one API request every 3 s; its retriever opens a new client for every search, which
#   loses that pause, and searches at once get HTTP 429;
# - SearXNG's Google CSE and Yahoo, the only engines that answer well from here, time out under ~16 queries at
#   once (20.10.2026), which leaves the subtopics nearly without web results. Tavily (Railway) copes and is not paced.
# search() runs in a worker thread (asyncio.to_thread), so waiting here does not hold up the event loop.
PACE = {"ArxivSearch": 3.0, "SearxSearch": 1.0}
# Engines that get no Cyrillic queries. gpt-researcher always searches the research's own query too, which is the
# user's Russian topic (or a detailed report's Russian subtopic); arXiv answers it with nothing useful, and slowly:
# 150 s against 5 s for an English query (20.10.2026), which held up every research step that sent one.
ENGLISH_ONLY = {"ArxivSearch"}
CYRILLIC = re.compile("[а-яё]", re.I)
_pace_locks = {engine: threading.Lock() for engine in PACE}
_last_search: dict[str, float] = {}


def _remember(results) -> None:
    if not isinstance(results, list):
        return
    if len(SEARCH_META) > 20000:
        SEARCH_META.clear()
    for r in results:
        url = isinstance(r, dict) and (r.get("href") or r.get("url"))
        if url:
            SEARCH_META[readable_url(url)] = {"title": str(r.get("title") or "").strip(), "snippet": str(r.get("body") or "").strip()}


def _wait_turn(engine: str) -> None:
    if engine not in PACE:
        return
    with _pace_locks[engine]:  # held while waiting, so this engine's searches start one at a time
        interval = PACE[engine]
        wait = _last_search.get(engine, 0.0) + interval - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        _last_search[engine] = time.monotonic()


def _patch_retrievers() -> None:
    for name in dir(retrievers):
        cls = getattr(retrievers, name)
        search = getattr(cls, "search", None)
        if not (isinstance(cls, type) and name.endswith("Search") and callable(search)) \
                or inspect.iscoroutinefunction(search) or getattr(search, "remembers", False):
            continue

        def remembering(self, *args, _search=search, _engine=name, **kwargs):
            if _engine in ENGLISH_ONLY and CYRILLIC.search(str(getattr(self, "query", ""))):
                return []
            _wait_turn(_engine)
            results = _search(self, *args, **kwargs)
            _remember(results)
            return results

        remembering.remembers = True
        cls.search = remembering


async def _select(query: str, urls: list[str], visited: set[str]) -> list[tuple[str, int]]:
    candidates = [{"url": u, **SEARCH_META.get(u, {})} for u in urls]
    # visited_urls is shared by the sub-researchers of a detailed report, so this caps
    # each domain across the whole research.
    response = await call("search", "POST", "/select", json={
        "query": query, "candidates": candidates, "domain_counts": Counter(host(u) for u in visited),
        "blocked": blocked.get(),
    })
    data = response.json()
    if meter := costs.current.get():
        meter.merge(data["usage"])
    return [(url, score) for url, score in data["selected"]]


async def _get_new_urls_filtered(self, url_set_input):
    """Replacement for ResearchConductor._get_new_urls: select before marking as visited."""
    candidates = list(dict.fromkeys(u for u in map(readable_url, url_set_input) if u not in self.researcher.visited_urls))
    if os.environ.get("SOURCE_FILTER", "on") == "off" or not candidates:
        selected = [(u, None) for u in candidates]
    else:
        query = self.researcher.query
        if self.researcher.parent_query:
            query = f"{self.researcher.parent_query} - {query}"
        selected = await _select(query, candidates, set(self.researcher.visited_urls))
        if len(selected) < len(candidates) and self.researcher.verbose:
            await stream_output("logs", "source_filter", f"🧹 Source filter: kept {len(selected)} of {len(candidates)} URLs\n",
                                self.researcher.websocket)

    new_urls = []
    for url, score in selected:
        if url in self.researcher.visited_urls:  # added by a parallel sub-query while we awaited the rating
            continue
        self.researcher.visited_urls.add(url)
        new_urls.append(url)
        if self.researcher.verbose:
            await stream_output("logs", "added_source_url",
                                f"✅ Added source url to research ({score if score is not None else '-'}/3): {url}\n",
                                self.researcher.websocket, True, url)
    return new_urls


async def _read_via_search(self, link, session):
    try:
        response = await call("search", "POST", "/read", json={"url": link}, timeout=180)
    except Exception as e:  # an unreachable search part costs this page, not the research
        logger.warning("Search part could not read %s: %s", link, e)
        return {"url": link, "raw_content": None, "image_urls": [], "title": ""}
    page = response.json()
    if page.get("raw_content") and (pages := read_pages.get()) is not None:
        pages[link] = {"text": page["raw_content"], "title": page.get("title") or ""}
    return page


_patch_retrievers()
ResearchConductor._get_new_urls = _get_new_urls_filtered
Scraper.extract_data_from_url = _read_via_search

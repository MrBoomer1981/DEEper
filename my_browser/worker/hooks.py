"""Where gpt-researcher meets the search part.

Importing this module patches gpt-researcher in the worker process:
- every retriever's search() remembers titles and snippets of its results (SEARCH_META), so the
  search part can rate candidates by more than their URL;
- ResearchConductor._get_new_urls (where search results become research sources, and later the
  references) asks the search part which candidates to take (POST /select);
- Scraper.extract_data_from_url reads every page through the search part (POST /read), which adds
  free fallbacks to the plain fetch.
The search engines themselves still run here, inside gpt-researcher.
"""

import contextvars
import inspect
import logging
import os
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


def _remember(results) -> None:
    if not isinstance(results, list):
        return
    if len(SEARCH_META) > 20000:
        SEARCH_META.clear()
    for r in results:
        url = isinstance(r, dict) and (r.get("href") or r.get("url"))
        if url:
            SEARCH_META[readable_url(url)] = {"title": str(r.get("title") or "").strip(), "snippet": str(r.get("body") or "").strip()}


def _patch_retrievers() -> None:
    for name in dir(retrievers):
        cls = getattr(retrievers, name)
        search = getattr(cls, "search", None)
        if not (isinstance(cls, type) and name.endswith("Search") and callable(search)) \
                or inspect.iscoroutinefunction(search) or getattr(search, "remembers", False):
            continue

        def remembering(self, *args, _search=search, **kwargs):
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
        return response.json()
    except Exception as e:  # an unreachable search part costs this page, not the research
        logger.warning("Search part could not read %s: %s", link, e)
        return {"url": link, "raw_content": None, "image_urls": [], "title": ""}


_patch_retrievers()
ResearchConductor._get_new_urls = _get_new_urls_filtered
Scraper.extract_data_from_url = _read_via_search

"""
Tavily-based web search wrapper.
Returns URLs and snippets for a list of queries.
"""
import asyncio
from typing import Dict, List

from tavily import TavilyClient

from bot.utils.logger import get_logger

logger = get_logger("web_search")


class WebSearch:
    def __init__(self, api_key: str, pages_per_query: int = 2) -> None:
        self.client = TavilyClient(api_key=api_key)
        self.pages_per_query = pages_per_query

    async def search_query(self, query: str) -> Dict:
        """
        Run a single Tavily search query asynchronously.

        Returns a dict with 'urls' and 'snippets'.
        """
        try:
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None,
                lambda: self.client.search(
                    query=query,
                    search_depth="advanced",
                    max_results=self.pages_per_query,
                    include_answer=True,
                    include_raw_content=False,
                ),
            )

            urls: List[str] = []
            snippets: List[str] = []

            for item in result.get("results", []):
                url = item.get("url", "")
                snippet = item.get("content", "")
                if url:
                    urls.append(url)
                if snippet:
                    snippets.append(f"[{url}] {snippet}")

            logger.info("Query '{}' → {} URLs", query[:60], len(urls))
            return {"urls": urls, "snippets": snippets, "answer": result.get("answer", "")}

        except Exception as e:
            logger.error("Search error for '{}': {}", query[:60], e)
            return {"urls": [], "snippets": [], "answer": ""}

    async def search_many(
        self, queries: List[str], max_concurrent: int = 3
    ) -> Dict[str, Dict]:
        """
        Run multiple queries concurrently.

        Returns a dict mapping query → result dict.
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def guarded(q: str) -> tuple:
            async with semaphore:
                result = await self.search_query(q)
                return q, result

        pairs = await asyncio.gather(*[guarded(q) for q in queries])
        results = {q: r for q, r in pairs}

        all_urls = sum(len(r["urls"]) for r in results.values())
        logger.info("Searched {} queries → {} total URLs", len(queries), all_urls)
        return results

"""
Async web scraper with retry logic, timeout handling, and HTML cleaning.
Uses aiohttp + BeautifulSoup.
"""
import asyncio
from typing import List, Optional

import aiohttp
from bs4 import BeautifulSoup

from bot.services.cache_manager import CacheManager
from bot.utils.logger import get_logger
from bot.utils.text_utils import clean_html_text

logger = get_logger("web_scraper")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

SKIP_EXTENSIONS = {
    ".pdf", ".doc", ".docx", ".xls", ".xlsx",
    ".zip", ".gz", ".tar", ".rar",
    ".jpg", ".jpeg", ".png", ".gif", ".mp4", ".mp3",
}


def _is_scrapable(url: str) -> bool:
    lower = url.lower().split("?")[0]
    return not any(lower.endswith(ext) for ext in SKIP_EXTENSIONS)


def _extract_text(html: str) -> str:
    """Parse HTML and extract meaningful text."""
    soup = BeautifulSoup(html, "lxml")

    # Remove boilerplate tags
    for tag in soup(["script", "style", "nav", "footer", "header", "aside",
                      "form", "noscript", "iframe", "svg"]):
        tag.decompose()

    # Prefer article / main content if available
    main = soup.find("article") or soup.find("main") or soup.find("div", {"id": "content"})
    target = main if main else soup.body if soup.body else soup

    text = target.get_text(separator=" ")
    return clean_html_text(text)


class WebScraper:
    def __init__(
        self,
        cache: CacheManager,
        timeout: int = 15,
        retries: int = 3,
    ) -> None:
        self.cache = cache
        self.timeout = aiohttp.ClientTimeout(total=timeout)
        self.retries = retries

    async def _fetch(self, session: aiohttp.ClientSession, url: str) -> Optional[str]:
        """Fetch a single URL with retry logic."""
        for attempt in range(1, self.retries + 1):
            try:
                async with session.get(
                    url, headers=HEADERS, timeout=self.timeout, allow_redirects=True
                ) as resp:
                    if resp.status != 200:
                        logger.warning("HTTP {} for {} (attempt {})", resp.status, url[:80], attempt)
                        return None
                    ct = resp.headers.get("Content-Type", "")
                    if "text" not in ct and "html" not in ct:
                        logger.debug("Skipping non-text content: {}", url[:80])
                        return None
                    html = await resp.text(errors="replace")
                    return html
            except asyncio.TimeoutError:
                logger.warning("Timeout for {} (attempt {})", url[:80], attempt)
            except aiohttp.ClientError as e:
                logger.warning("Client error for {}: {} (attempt {})", url[:80], e, attempt)
            except Exception as e:
                logger.error("Unexpected error fetching {}: {}", url[:80], e)
                return None

            if attempt < self.retries:
                await asyncio.sleep(1.5 * attempt)

        return None

    async def scrape(self, url: str) -> Optional[str]:
        """Return cleaned text for a URL. Uses cache if available."""
        if not _is_scrapable(url):
            logger.debug("Skipping non-scrapable URL: {}", url[:80])
            return None

        cached = self.cache.get(url)
        if cached:
            return cached

        async with aiohttp.ClientSession() as session:
            html = await self._fetch(session, url)

        if not html:
            return None

        text = _extract_text(html)
        if len(text) < 100:
            logger.debug("Too little content extracted from {}", url[:80])
            return None

        self.cache.set(url, text)
        logger.info("Scraped {} chars from {}", len(text), url[:80])
        return text

    async def scrape_many(self, urls: List[str], max_concurrent: int = 5) -> List[str]:
        """
        Scrape multiple URLs concurrently.
        Returns list of non-empty text results.
        """
        semaphore = asyncio.Semaphore(max_concurrent)

        async def guarded(url: str) -> Optional[str]:
            async with semaphore:
                return await self.scrape(url)

        results = await asyncio.gather(*[guarded(u) for u in urls])
        texts = [r for r in results if r]
        logger.info("Scraped {}/{} pages successfully", len(texts), len(urls))
        return texts

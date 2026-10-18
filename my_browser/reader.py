"""Second chance for pages gpt-researcher's scraper could not read.

Publishers and other bot-protected sites answer a plain fetch with 403 or an empty page, and they are
often the best-rated sources: the eval baseline lost 29% of selected pages, two thirds of them 3/3.
After a failed fetch this module tries, in order: the open-access full text from Europe PMC (PubMed
Central links) or from an open copy found via OpenAlex (links carrying a DOI), Jina Reader (any URL,
read by a headless browser), and finally the abstract from OpenAlex. Every step is free.

Per-domain outcomes are kept in reports/read_stats.json: domains whose plain fetch keeps failing go
straight to the fallbacks (no 403 retries and timeouts), and sources.rank() demotes domains that stay
unreadable even then. Importing the module wraps Scraper.extract_data_from_url.
"""

import asyncio
import json
import logging
import os
import re
import threading
import time
from urllib.parse import unquote, urlparse
from xml.etree import ElementTree

import httpx
from gpt_researcher.scraper.scraper import Scraper
from gpt_researcher.utils.url_security import UnsafeURLError, validate_url

from my_browser.pipeline import REPORTS_DIR

logger = logging.getLogger(__name__)

STATS_PATH = REPORTS_DIR / "read_stats.json"
JINA_URL = "https://r.jina.ai/"
OPENALEX_WORK = "https://api.openalex.org/works/doi:{doi}"
EUROPE_PMC_XML = "https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
USER_AGENT = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0 Safari/537.36"
MIN_CHARS = 500  # less than this is an error page, a cookie wall or a teaser
MAX_CHARS = 150_000  # a whole book or journal issue would only slow down context compression

DOI = re.compile(r"\b(10\.\d{4,9}/[^\s?#&]+)", re.I)
DOI_TAIL = re.compile(r"/(?:full|abstract|pdf|epdf|html|meta|references)$", re.I)
PMCID = re.compile(r"\b(PMC\d+)\b", re.I)
JINA_ERROR = re.compile(r"^Warning: Target URL returned error", re.M)
BLOCK_PAGE = re.compile(r"just a moment|verify you are human|enable javascript and cookies|are you a robot|"
                        r"access denied|unusual traffic", re.I)


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


class ReadStats:
    """Per-domain reading outcomes, kept across runs: direct_ok, direct_fail, rescued, lost."""

    def __init__(self, path=STATS_PATH) -> None:
        self.path = path
        self.lock = threading.Lock()
        try:
            self.data: dict[str, dict[str, int]] = json.loads(path.read_text("utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def record(self, url: str, outcome: str) -> None:
        with self.lock:
            row = self.data.setdefault(host(url), {})
            row[outcome] = row.get(outcome, 0) + 1
            try:
                self.path.write_text(json.dumps(self.data, ensure_ascii=False, indent=1), "utf-8")
            except OSError as e:
                logger.warning("Read stats not saved: %s", e)

    def skip_direct(self, url: str) -> bool:
        """The plain fetch failed at least 80% of 3+ tries on this domain."""
        row = self.data.get(host(url), {})
        tries = row.get("direct_ok", 0) + row.get("direct_fail", 0)
        return tries >= 3 and row.get("direct_fail", 0) >= 0.8 * tries

    def unreadable(self, url: str) -> bool:
        """At least 80% of 3+ pages from this domain stayed unreadable even through the fallbacks."""
        row = self.data.get(host(url), {})
        tries = row.get("direct_ok", 0) + row.get("rescued", 0) + row.get("lost", 0)
        return tries >= 3 and row.get("lost", 0) >= 0.8 * tries


class Pace:
    """At most one call per `interval` seconds across all event loops (each research runs its own)."""

    def __init__(self, interval: float) -> None:
        self.interval = interval
        self.next_at = 0.0
        self.lock = threading.Lock()

    async def wait(self) -> None:
        with self.lock:
            start = max(time.monotonic(), self.next_at)
            self.next_at = start + self.interval
        await asyncio.sleep(start - time.monotonic())


stats = ReadStats()
# Jina Reader allows 20 requests a minute without a key and 500 with a free one.
jina_pace = Pace(0.15 if os.environ.get("JINA_API_KEY") else 3.1)


def find_doi(url: str) -> str | None:
    path = unquote(urlparse(url).path)
    if host(url) in ("doi.org", "dx.doi.org"):
        return DOI_TAIL.sub("", path.strip("/")) or None
    match = DOI.search(path)
    return DOI_TAIL.sub("", match.group(1).removesuffix(".pdf")) if match else None


def usable(text: str | None) -> bool:
    return bool(text) and len(text) >= MIN_CHARS and not BLOCK_PAGE.search(text[:3000])


async def europe_pmc(client: httpx.AsyncClient, pmcid: str) -> tuple[str, str] | None:
    """Full text of an open-access PubMed Central article (JATS XML)."""
    response = await client.get(EUROPE_PMC_XML.format(pmcid=pmcid.upper()), timeout=20)
    if response.status_code != 200:
        return None
    root = ElementTree.fromstring(response.content)
    paragraphs = (" ".join("".join(p.itertext()).split()) for p in root.iter("p"))
    text = "\n\n".join(p for p in paragraphs if p)
    if not usable(text):
        return None
    title = root.find(".//article-title")
    return text, " ".join("".join(title.itertext()).split()) if title is not None else ""


async def openalex_work(client: httpx.AsyncClient, doi: str) -> dict | None:
    response = await client.get(OPENALEX_WORK.format(doi=doi), timeout=15)
    return response.json() if response.status_code == 200 else None


def open_copies(work: dict, url: str) -> list[str]:
    """Open-access copies of the work other than the page that just failed."""
    locations = [work.get("best_oa_location") or {}, *(work.get("locations") or [])]
    copies = []
    for location in locations:
        if not location.get("is_oa"):
            continue
        for candidate in (location.get("pdf_url"), location.get("landing_page_url")):
            if candidate and candidate != url and host(candidate) not in ("doi.org", "dx.doi.org", host(url)):
                copies.append(candidate)
    return list(dict.fromkeys(copies))[:3]


def abstract(work: dict) -> str:
    """OpenAlex keeps abstracts as an inverted index: word -> positions."""
    positions = sorted((pos, word) for word, places in (work.get("abstract_inverted_index") or {}).items() for pos in places)
    return " ".join(word for _, word in positions)


async def jina(client: httpx.AsyncClient, url: str) -> tuple[str, str] | None:
    """The page as read by Jina Reader's headless browser, in markdown."""
    await jina_pace.wait()
    headers = {"X-Timeout": "25"}
    if key := os.environ.get("JINA_API_KEY"):
        headers["Authorization"] = f"Bearer {key}"
    response = await client.get(JINA_URL + url, headers=headers, timeout=40)
    if response.status_code != 200 or JINA_ERROR.search(response.text[:1500]):
        return None
    title = re.search(r"^Title:\s*(.*)$", response.text[:1500], re.M)
    text = response.text.split("Markdown Content:", 1)[-1].strip()
    return (text, title.group(1).strip() if title else "") if usable(text) else None


async def rescue(url: str, scraper: Scraper, session) -> tuple[str, str, str] | None:
    """(text, title, via) from the first fallback that works, or None."""
    async with httpx.AsyncClient(headers={"User-Agent": USER_AGENT}, follow_redirects=True) as client:
        pmcid = PMCID.search(url)
        if pmcid and host(url).endswith(("ncbi.nlm.nih.gov", "europepmc.org")):
            try:
                if found := await europe_pmc(client, pmcid.group(1)):
                    return (*found, "europepmc")
            except (httpx.HTTPError, ElementTree.ParseError) as e:
                logger.info("Europe PMC failed for %s: %s", url, e)

        work = None
        if doi := find_doi(url):
            try:
                work = await openalex_work(client, doi)
            except (httpx.HTTPError, ValueError) as e:
                logger.info("OpenAlex lookup failed for %s: %s", doi, e)
            for copy in open_copies(work or {}, url):
                result = await _extract(scraper, copy, session)
                if result and usable(result.get("raw_content")):
                    return result["raw_content"], result.get("title") or work.get("title") or "", "open-access"

        try:
            if found := await jina(client, url):
                return (*found, "jina")
        except httpx.HTTPError as e:
            logger.info("Jina Reader failed for %s: %s", url, e)

        if work and len(text := abstract(work)) >= 200:
            title = work.get("title") or ""
            return f"{title}\n\n{text}", title, "abstract"
    return None


_extract = Scraper.extract_data_from_url


async def _extract_with_fallbacks(self, link, session):
    try:
        validate_url(link)
    except UnsafeURLError:
        return await _extract(self, link, session)  # upstream rejects and logs it
    result = None
    if not stats.skip_direct(link):
        result = await _extract(self, link, session)
        if result and result.get("raw_content"):
            stats.record(link, "direct_ok")
            return result
        stats.record(link, "direct_fail")
    try:
        found = await rescue(link, self, session)
    except Exception as e:  # a fallback must never break the research
        logger.warning("Fallback reading failed for %s: %s", link, e)
        found = None
    if not found:
        stats.record(link, "lost")
        logger.info("Unreadable after fallbacks: %s", link)
        return result or {"url": link, "raw_content": None, "image_urls": [], "title": ""}
    text, title, via = found
    stats.record(link, "rescued")
    logger.info("Read via %s: %s (%d chars)", via, link, len(text))
    return {"url": link, "raw_content": text[:MAX_CHARS], "image_urls": [], "title": title}


Scraper.extract_data_from_url = _extract_with_fallbacks

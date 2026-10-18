"""Source quality filter applied before pages are scraped.

Candidate URLs found for a search query are scored 0-3:
  3  primary/authoritative (research, government, international bodies, major media/analysts)
  2  solid secondary (trade press, encyclopedias, reputable company pages)
  1  weak (SEO blogs, vendor marketing, aggregators, forums)
  0  useless (shops, social/video, unrelated, user-blocked)
Known domains are scored from the lists below; the rest by one cheap LLM call per query.
Zeros are dropped; ones are used only when a query found nothing better; the best go first.

Importing this module patches gpt-researcher's ResearchConductor._get_new_urls, the point
where search results become research sources (and later the reference list).
"""

import logging
import os
import re
from urllib.parse import urlparse

import json_repair
from gpt_researcher.actions import stream_output
from gpt_researcher.skills.researcher import ResearchConductor
from gpt_researcher.utils.llm import create_chat_completion

from my_browser.pipeline import REPORTS_DIR

logger = logging.getLogger(__name__)

MAX_SOURCES_PER_QUERY = int(os.environ.get("MAX_SOURCES_PER_QUERY", 8))
WEAK_FALLBACK = 3  # weak (1) sources are used only when a query found nothing better, and at most this many
BLOCKLIST_PATH = REPORTS_DIR / "blocked_domains.txt"  # on Railway this lives on the volume

TOP_TIER_HOST = re.compile(r"(^|\.)(gov|edu|int|mil)(\.[a-z]{2})?$|(^|\.)ac\.[a-z]{2}$|(^|\.)gov\.ru$")

DOMAIN_SCORES = {
    3: [
        # research, publishers, indexes
        "arxiv.org", "doi.org", "openalex.org", "semanticscholar.org", "nature.com", "science.org",
        "cell.com", "thelancet.com", "nejm.org", "springer.com", "link.springer.com", "sciencedirect.com",
        "elsevier.com", "wiley.com", "onlinelibrary.wiley.com", "tandfonline.com", "sagepub.com",
        "ieee.org", "acm.org", "acs.org", "pubs.acs.org", "rsc.org", "iop.org", "aps.org", "pnas.org",
        "plos.org", "biorxiv.org", "medrxiv.org", "ssrn.com", "nber.org", "jstor.org", "ncbi.nlm.nih.gov",
        # international organizations and research institutes
        "europa.eu", "un.org", "who.int", "worldbank.org", "imf.org", "oecd.org", "iea.org", "irena.org",
        "wto.org", "ilo.org", "unesco.org", "bis.org", "ipcc.ch", "nrel.gov", "brookings.edu",
        "rand.org", "pewresearch.org", "ourworldindata.org", "cern.ch", "mpg.de", "fraunhofer.de",
        # major news agencies and analysts
        "reuters.com", "apnews.com", "bloomberg.com", "ft.com", "economist.com", "wsj.com",
        "nytimes.com", "bbc.com", "bbc.co.uk", "theguardian.com", "mckinsey.com", "bcg.com",
        "about.bnef.com", "spglobal.com", "woodmac.com",
        # authoritative Russian-language sources
        "ras.ru", "hse.ru", "msu.ru", "cbr.ru", "rosstat.gov.ru", "interfax.ru", "tass.ru",
        "kommersant.ru", "vedomosti.ru", "rbc.ru",
    ],
    2: [
        "wikipedia.org", "britannica.com", "statista.com", "idtechex.com", "techcrunch.com",
        "arstechnica.com", "wired.com", "technologyreview.com", "spectrum.ieee.org", "nasdaq.com",
        "cnbc.com", "forbes.com", "fortune.com", "axios.com", "politico.com", "theverge.com",
        "electrek.co", "canarymedia.com", "cleantechnica.com", "pv-magazine.com", "researchgate.net",
        "mdpi.com", "frontiersin.org", "github.com", "cyberleninka.ru", "elibrary.ru", "habr.com",
        "ixbt.com", "3dnews.ru", "cnews.ru",
    ],
    0: [
        "youtube.com", "youtu.be", "tiktok.com", "instagram.com", "facebook.com", "pinterest.com",
        "x.com", "twitter.com", "amazon.com", "aliexpress.com", "ebay.com", "etsy.com", "temu.com",
        "ozon.ru", "wildberries.ru", "avito.ru",
    ],
}

RATE_PROMPT = """You rate web sources for a research report on: "{query}".
For each URL give a credibility score:
3 - primary or authoritative: research papers, universities, government and regulators, international organizations, official statistics, major reputable news agencies and analyst firms.
2 - solid secondary: established trade or industry media, encyclopedias, reputable companies' official or technical pages.
1 - weak: SEO blogs, vendor marketing and product pages, affiliate or review sites, aggregators, forums, generic listicles.
0 - useless: unrelated to the topic, shops, spam, login or error pages.
Judge by the domain and the URL path.

{urls}

Return ONLY a JSON array of {count} integers in the same order, e.g. [3, 1, 0]."""


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def _matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith("." + domain)


def blocked_domains() -> list[str]:
    if not BLOCKLIST_PATH.exists():
        return []
    return [line.strip().lower() for line in BLOCKLIST_PATH.read_text("utf-8").splitlines() if line.strip()]


def block(domain: str) -> str:
    domain = host(domain if "://" in domain else f"https://{domain}")
    if domain and domain not in blocked_domains():
        BLOCKLIST_PATH.parent.mkdir(exist_ok=True)
        with BLOCKLIST_PATH.open("a", encoding="utf-8") as f:
            f.write(domain + "\n")
    return domain


def unblock(domain: str) -> bool:
    domain = host(domain if "://" in domain else f"https://{domain}")
    current = blocked_domains()
    if domain not in current:
        return False
    BLOCKLIST_PATH.write_text("".join(d + "\n" for d in current if d != domain), "utf-8")
    return True


def static_score(url: str, blocked: list[str]) -> int | None:
    """Score from the known lists, or None when the domain is unknown."""
    hostname = host(url)
    if any(_matches(hostname, d) for d in blocked):
        return 0
    if TOP_TIER_HOST.search(hostname):
        return 3
    for score, domains in DOMAIN_SCORES.items():
        if any(_matches(hostname, d) for d in domains):
            return score
    return None


async def llm_scores(urls: list[str], query: str, cfg) -> list[int] | None:
    listing = "\n".join(f"{i + 1}. {u}" for i, u in enumerate(urls))
    try:
        response = await create_chat_completion(
            messages=[{"role": "user", "content": RATE_PROMPT.format(query=query, urls=listing, count=len(urls))}],
            model=cfg.fast_llm_model,
            llm_provider=cfg.fast_llm_provider,
            temperature=0,
            max_tokens=200,
            llm_kwargs=cfg.llm_kwargs,
        )
        scores = json_repair.loads(response)
        if isinstance(scores, list) and len(scores) == len(urls):
            return [max(0, min(3, int(s))) for s in scores]
        logger.warning("Source rating: unexpected answer %r", response[:200])
    except Exception as e:
        logger.warning("Source rating failed, using known domains only: %s", e)
    return None


async def rank(urls: list[str], query: str, cfg) -> list[tuple[str, int]]:
    """Score and select URLs, best first."""
    blocked = blocked_domains()
    scores = {u: static_score(u, blocked) for u in urls}
    unknown = [u for u, s in scores.items() if s is None]
    if unknown:
        rated = await llm_scores(unknown, query, cfg)
        for u, s in zip(unknown, rated or [1] * len(unknown)):
            scores[u] = s
    good = sorted(((u, s) for u, s in scores.items() if s >= 2), key=lambda item: item[1], reverse=True)
    weak = [(u, s) for u, s in scores.items() if s == 1]
    return good[:MAX_SOURCES_PER_QUERY] if good else weak[:WEAK_FALLBACK]


async def _get_new_urls_filtered(self, url_set_input):
    """Replacement for ResearchConductor._get_new_urls: rank before marking as visited."""
    candidates = list(dict.fromkeys(u for u in url_set_input if u not in self.researcher.visited_urls))
    if os.environ.get("SOURCE_FILTER", "on") == "off" or not candidates:
        selected = [(u, None) for u in candidates]
    else:
        query = self.researcher.query
        if self.researcher.parent_query:
            query = f"{self.researcher.parent_query} - {query}"
        selected = await rank(candidates, query, self.researcher.cfg)
        dropped = len(candidates) - len(selected)
        if dropped and self.researcher.verbose:
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


ResearchConductor._get_new_urls = _get_new_urls_filtered

"""Source selection, the search part's POST /select: which search results become research sources.

Every candidate URL found for a search query gets a 0-3 score from one cheap LLM call that sees
its title and snippet (the worker captures them from the retrievers) and judges relevance to the
research topic together with credibility and freshness:
  3  highly relevant and primary/authoritative
  2  relevant and solid
  1  weak (SEO blogs, vendor marketing, aggregators, forums) or only tangential
  0  off-topic or useless (shops, spam, a paper about something else that shares some words)
Known domains (DOMAIN_SCORES) only raise the score of a relevant source and never rescue an
off-topic one; social/video/shops and the user's blocklist are dropped without asking the model,
and domains that stay unreadable (reader.stats) lose a point.
Selection: zeros out; ones only when a query found nothing better (at most WEAK_FALLBACK); at most
PER_DOMAIN_QUERY per domain per query and PER_DOMAIN_RESEARCH per research, so one site
(typically arXiv) cannot crowd out the rest; best first, at most MAX_SOURCES_PER_QUERY.
"""

import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone

import json_repair
from gpt_researcher.config import Config
from gpt_researcher.utils.llm import create_chat_completion

from my_browser.common.settings import CONFIG_PATH
from my_browser.common.text import host
from my_browser.search.reader import stats as read_stats

logger = logging.getLogger(__name__)

MAX_SOURCES_PER_QUERY = int(os.environ.get("MAX_SOURCES_PER_QUERY", 8))
PER_DOMAIN_QUERY = int(os.environ.get("PER_DOMAIN_QUERY", 2))
PER_DOMAIN_RESEARCH = int(os.environ.get("PER_DOMAIN_RESEARCH", 5))
WEAK_FALLBACK = 3

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

RATE_PROMPT = """You select sources for a research report on: "{query}". Today is {today}.
Score every search result:
3 - highly relevant to the topic AND primary or authoritative: research, official data, regulators, major news agencies or analysts, the company itself.
2 - relevant and solid: trade or industry press, encyclopedias, reputable companies' technical pages.
1 - weak: only tangential, or SEO blogs, vendor marketing, aggregators, forums, listicles.
0 - off-topic or useless: about something else even if some words match, shops, spam, error pages.
If the topic is about the current state of things, clearly outdated results get at most 1 unless they are foundational.
[known] marks domains known for credibility; that says nothing about relevance.

{listing}

Return ONLY a JSON array of {count} integers in the same order, e.g. [3, 1, 0]."""


def _matches(hostname: str, domain: str) -> bool:
    return hostname == domain or hostname.endswith("." + domain)


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


def _describe(i: int, candidate: dict, known: bool) -> str:
    lines = [f"{i}. {candidate['url']}" + (" [known]" if known else "")]
    if candidate.get("title"):
        lines.append(f"   title: {candidate['title'][:160]}")
    if candidate.get("snippet"):
        lines.append(f"   snippet: {' '.join(candidate['snippet'].split())[:240]}")
    return "\n".join(lines)


async def llm_scores(candidates: list[dict], query: str, known: set[str]) -> dict[str, int] | None:
    cfg = Config(str(CONFIG_PATH))
    urls = [c["url"] for c in candidates]
    listing = "\n".join(_describe(i + 1, c, c["url"] in known) for i, c in enumerate(candidates))
    today = datetime.now(timezone.utc).strftime("%B %d, %Y")
    try:
        response = await create_chat_completion(
            messages=[{"role": "user", "content": RATE_PROMPT.format(query=query, today=today, listing=listing, count=len(urls))}],
            model=cfg.fast_llm_model,
            llm_provider=cfg.fast_llm_provider,
            temperature=0,
            max_tokens=400,
            llm_kwargs=cfg.llm_kwargs,
        )
        scores = json_repair.loads(response)
        if isinstance(scores, list) and len(scores) == len(urls):
            return {u: max(0, min(3, int(s))) for u, s in zip(urls, scores)}
        logger.warning("Source rating: unexpected answer %r", response[:200])
    except Exception as e:
        logger.warning("Source rating failed, using known domains only: %s", e)
    return None


async def rank(candidates: list[dict], query: str, domain_counts: Counter, blocked: list[str]) -> list[tuple[str, int]]:
    """Score and select candidates ({url, title, snippet}), best first."""
    static = {c["url"]: static_score(c["url"], blocked) for c in candidates}
    to_rate = [c for c in candidates if static[c["url"]] != 0]
    known = {c["url"] for c in to_rate if static[c["url"]]}
    rated = await llm_scores(to_rate, query, known) if to_rate else {}
    scores = {}
    for c in to_rate:
        u = c["url"]
        if rated is None:  # rating failed: known domains keep their tier, the rest count as weak
            scores[u] = static[u] or 1
        elif rated[u] > 0:  # authority raises a relevant result, never rescues an off-topic one
            scores[u] = max(rated[u], static[u] or 0)
    for u in scores:  # a domain that stays unreadable even through the reader's fallbacks loses a point
        if scores[u] > 1 and read_stats.unreadable(u):
            scores[u] -= 1
    good = sorted(((u, s) for u, s in scores.items() if s >= 2), key=lambda item: item[1], reverse=True)
    pool = good or [(u, s) for u, s in scores.items() if s == 1][:WEAK_FALLBACK]

    selected, per_query = [], Counter()
    for u, s in pool:
        d = host(u)
        if per_query[d] >= PER_DOMAIN_QUERY or domain_counts[d] + per_query[d] >= PER_DOMAIN_RESEARCH:
            continue
        selected.append((u, s))
        per_query[d] += 1
        if len(selected) >= MAX_SOURCES_PER_QUERY:
            break
    return selected

"""Source selection applied before pages are scraped.

Every candidate URL found for a search query gets a 0-3 score from one cheap LLM call that sees
its title and snippet (captured from the retrievers) and judges relevance to the research topic
together with credibility and freshness:
  3  highly relevant and primary/authoritative
  2  relevant and solid
  1  weak (SEO blogs, vendor marketing, aggregators, forums) or only tangential
  0  off-topic or useless (shops, spam, a paper about something else that shares some words)
Known domains (DOMAIN_SCORES) only raise the score of a relevant source and never rescue an
off-topic one; social/video/shops and the user's blocklist are dropped without asking the model.
Selection: zeros out; ones only when a query found nothing better (at most WEAK_FALLBACK); at most
PER_DOMAIN_QUERY per domain per query and PER_DOMAIN_RESEARCH per research, so one site
(typically arXiv) cannot crowd out the rest; best first, at most MAX_SOURCES_PER_QUERY.

Importing this module patches gpt-researcher: the retrievers' search() remembers titles and
snippets, and ResearchConductor._get_new_urls (where search results become research sources,
and later the references) applies the selection.
"""

import inspect
import logging
import os
import re
from collections import Counter
from datetime import datetime, timezone
from urllib.parse import quote, urlparse

import gpt_researcher.retrievers as retrievers
import json_repair
from gpt_researcher.actions import stream_output
from gpt_researcher.skills.researcher import ResearchConductor
from gpt_researcher.utils.llm import create_chat_completion

from my_browser.pipeline import REPORTS_DIR

logger = logging.getLogger(__name__)

MAX_SOURCES_PER_QUERY = int(os.environ.get("MAX_SOURCES_PER_QUERY", 8))
PER_DOMAIN_QUERY = int(os.environ.get("PER_DOMAIN_QUERY", 2))
PER_DOMAIN_RESEARCH = int(os.environ.get("PER_DOMAIN_RESEARCH", 5))
WEAK_FALLBACK = 3
BLOCKLIST_PATH = REPORTS_DIR / "blocked_domains.txt"  # on Railway this lives on the volume
SEARCH_META: dict[str, dict] = {}  # url -> title/snippet from the last searches that returned it

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


_ENCODED_RUN = re.compile(r"(?:%[0-9A-Fa-f]{2})+")


def readable_url(url: str) -> str:
    """Decode percent-encoded non-ASCII text (Cyrillic slugs) but keep ASCII escapes like %2F or %20.

    Encoded Cyrillic costs the writer model 3-4 tokens per letter; long reports with many such
    links hit the output limit and got cut off mid-reference. Requests re-encodes these URLs.
    """
    def decode(match: re.Match) -> str:
        raw = bytes.fromhex(match.group(0).replace("%", ""))
        if all(b < 0x80 for b in raw):
            return match.group(0)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return match.group(0)
        return "".join(quote(ch, safe="") if ord(ch) < 0x80 else ch for ch in text)

    return _ENCODED_RUN.sub(decode, url)


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


def _describe(i: int, url: str, known: bool) -> str:
    meta = SEARCH_META.get(url, {})
    lines = [f"{i}. {url}" + (" [known]" if known else "")]
    if meta.get("title"):
        lines.append(f"   title: {meta['title'][:160]}")
    if meta.get("snippet"):
        lines.append(f"   snippet: {' '.join(meta['snippet'].split())[:240]}")
    return "\n".join(lines)


async def llm_scores(urls: list[str], query: str, cfg, known: set[str]) -> dict[str, int] | None:
    listing = "\n".join(_describe(i + 1, u, u in known) for i, u in enumerate(urls))
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


async def rank(urls: list[str], query: str, cfg, domain_counts: Counter | None = None) -> list[tuple[str, int]]:
    """Score and select URLs, best first."""
    blocked = blocked_domains()
    static = {u: static_score(u, blocked) for u in urls}
    to_rate = [u for u in urls if static[u] != 0]
    rated = await llm_scores(to_rate, query, cfg, {u for u in to_rate if static[u]}) if to_rate else {}
    scores = {}
    for u in to_rate:
        if rated is None:  # rating failed: known domains keep their tier, the rest count as weak
            scores[u] = static[u] or 1
        elif rated[u] > 0:  # authority raises a relevant result, never rescues an off-topic one
            scores[u] = max(rated[u], static[u] or 0)
    good = sorted(((u, s) for u, s in scores.items() if s >= 2), key=lambda item: item[1], reverse=True)
    pool = good or [(u, s) for u, s in scores.items() if s == 1][:WEAK_FALLBACK]

    domain_counts = domain_counts or Counter()
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


async def _get_new_urls_filtered(self, url_set_input):
    """Replacement for ResearchConductor._get_new_urls: select before marking as visited."""
    candidates = list(dict.fromkeys(u for u in map(readable_url, url_set_input) if u not in self.researcher.visited_urls))
    if os.environ.get("SOURCE_FILTER", "on") == "off" or not candidates:
        selected = [(u, None) for u in candidates]
    else:
        query = self.researcher.query
        if self.researcher.parent_query:
            query = f"{self.researcher.parent_query} - {query}"
        # visited_urls is shared by the sub-researchers of a detailed report, so this caps
        # each domain across the whole research.
        selected = await rank(candidates, query, self.researcher.cfg, Counter(host(u) for u in self.researcher.visited_urls))
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


_patch_retrievers()
ResearchConductor._get_new_urls = _get_new_urls_filtered

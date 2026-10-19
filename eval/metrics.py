"""Report metrics for eval/run.py, computed from a saved report and the run's log lines (no LLM calls)."""

import re
from collections import Counter

from my_browser.common.text import host
from my_browser.search.sources import static_score
from my_browser.worker.factcheck import LINK, text_and_references, units

CYRILLIC = re.compile("[а-яё]", re.I)
RUSSIAN_TLDS = (".ru", ".su", ".рф", ".xn--p1ai", ".by", ".kz")
ADDED = re.compile(r"Added source url to research(?: \(([^)]*)\))?: (\S+)")
FAILED = re.compile(r"for (\S+), treating as fetch failure|PyMuPDFScraper retry also failed for (\S+)|Error processing (\S+?): ")
RESCUED = re.compile(r"Read via (\S+): (\S+)")  # my_browser.search.reader
LOST = re.compile(r"Unreadable after fallbacks: (\S+)")


def share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def is_russian(url: str, title: str = "") -> bool:
    return bool(CYRILLIC.search(title) or CYRILLIC.search(url)) or host(url).endswith(RUSSIAN_TLDS)


def sources(urls: list[str], titles: dict[str, str]) -> dict:
    domains = Counter(host(u) for u in urls)
    top, top_count = domains.most_common(1)[0] if domains else ("", 0)
    return {
        "sources": len(urls),
        "domains": len(domains),
        "top_domain": top,
        "top_domain_share": share(top_count, len(urls)),
        "english": share(sum(not is_russian(u, titles.get(u, "")) for u in urls), len(urls)),
        "authoritative": share(sum(static_score(u, []) == 3 for u in urls), len(urls)),
    }


def citations(urls: list[str], markdown: str) -> dict:
    text, references = text_and_references(markdown)
    paragraphs = [u for u in units(text) if len(u.split()) >= 25]
    return {
        "in_report": share(sum(u in text + references for u in urls), len(urls)),
        "linked_paragraphs": share(sum(bool(LINK.search(p)) for p in paragraphs), len(paragraphs)),
    }


def scrape_log(lines: list[str]) -> dict:
    """Sources the filter accepted (with their score), the ones read only through reader's fallbacks,
    and the ones nothing could read."""
    added = {}
    for line in lines:
        if match := ADDED.search(line):
            added[match.group(2)] = (match.group(1) or "").split("/")[0]
    direct_failed = {next(filter(None, match.groups())) for line in lines if (match := FAILED.search(line))}
    rescued = {match.group(2): match.group(1) for line in lines if (match := RESCUED.search(line))}
    lost = {match.group(1) for line in lines if (match := LOST.search(line))}
    failed = ((direct_failed - set(rescued)) | lost) & set(added)
    return {
        "attempted": len(added),
        "score3": share(sum(score == "3" for score in added.values()), len(added)),
        "failed": share(len(failed), len(added)),
        "rescued": share(len(set(rescued) & set(added)), len(added)),
        "rescued_via": dict(Counter(via for url, via in rescued.items() if url in added)),
    }

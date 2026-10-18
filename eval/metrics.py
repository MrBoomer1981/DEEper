"""Report metrics for eval/run.py, computed from a saved report and the run's log lines (no LLM calls)."""

import re
from collections import Counter

from my_browser.sources import host, static_score
from my_browser.stemming import tokenize
from my_browser.suggest import body

LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)")
REFERENCES = re.compile(r"^#{1,3}\s*(?:список\s+)?(?:источник|литератур|ссылк|reference|bibliograph)", re.I | re.M)
CYRILLIC = re.compile("[а-яё]", re.I)
RUSSIAN_TLDS = (".ru", ".su", ".рф", ".xn--p1ai", ".by", ".kz")
ADDED = re.compile(r"Added source url to research(?: \(([^)]*)\))?: (\S+)")
FAILED = re.compile(r"for (\S+), treating as fetch failure|PyMuPDFScraper retry also failed for (\S+)|Error processing (\S+?): ")
RESCUED = re.compile(r"Read via (\S+): (\S+)")  # my_browser.reader
LOST = re.compile(r"Unreadable after fallbacks: (\S+)")
SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[«\"(\[A-ZА-ЯЁ0-9])")
LIST_ITEM = re.compile(r"\s*(?:[-*+]|\d+[.)])\s")
NUMBER = re.compile(r"\d+(?:[.,]\d+)?")
LATIN_NAME = re.compile(r"[A-Za-z][A-Za-z0-9\-]{2,}")


def share(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def text_and_references(markdown: str) -> tuple[str, str]:
    """The report's text and its reference list (from the last references heading), follow-ups dropped."""
    report = body(markdown)
    headings = list(REFERENCES.finditer(report))
    if not headings:
        return report, ""
    return report[:headings[-1].start()], report[headings[-1].start():]


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


def _units(text: str) -> list[str]:
    """Paragraphs of the text, with every list item as its own unit; headings and tables skipped."""
    units = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line for line in block.splitlines() if line.strip()]
        if not lines or lines[0].lstrip().startswith(("#", "|")):
            continue
        if all(LIST_ITEM.match(line) for line in lines):
            units += [" ".join(line.split()) for line in lines]
        else:
            units.append(" ".join(block.split()))
    return units


def citations(urls: list[str], markdown: str) -> dict:
    text, references = text_and_references(markdown)
    paragraphs = [u for u in _units(text) if len(u.split()) >= 25]
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


def citation_pairs(markdown: str) -> list[tuple[str, str]]:
    """(statement, url) for every sentence of the report's text that links a source."""
    text, _ = text_and_references(markdown)
    pairs = []
    for unit in _units(text):
        for sentence in SENTENCE_END.split(unit):
            statement = " ".join(LINK.sub(r"\1", sentence).replace("**", "").split()).lstrip("-*+ ")
            if len(statement) < 40:
                continue
            pairs += [(statement, url) for url in dict.fromkeys(m.group(2) for m in LINK.finditer(sentence))]
    return pairs


def excerpts(statement: str, page: str, count: int = 4, size: int = 120) -> list[str]:
    """The page chunks sharing the most numbers, Latin names and word stems with the statement.

    Statements are Russian and many pages English, so numbers and names carry most of the match."""
    words = page.split()[:30000]
    chunks = [" ".join(words[i:i + size]) for i in range(0, max(len(words), 1), size - 20)]
    numbers = {n.replace(",", ".") for n in NUMBER.findall(statement)}
    names = {w.lower() for w in LATIN_NAME.findall(statement)}
    stems = set(tokenize(statement))

    def score(chunk: str) -> int:
        lower = chunk.lower().replace(",", ".")
        return 3 * sum(n in lower for n in numbers) + 2 * sum(n in lower for n in names) + len(stems & set(tokenize(chunk)))

    best = sorted(range(len(chunks)), key=lambda i: score(chunks[i]), reverse=True)[:count]
    return [chunks[i] for i in sorted(best)]

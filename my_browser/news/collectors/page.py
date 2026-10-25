"""Pages without a feed: a program's page (MATS, a summer school), a company's internships, a list of hackathons.

The page's main text (without menus, headers, footers, forms and scripts) is fingerprinted at every poll, once a
day. The first poll stores the text as a post: the conditions as they are. A change stores a new post. A program's
page gives its whole new text, with what changed at the end: the embedding reads the start, which stays the same,
so the post joins the page's story, and the model reads the conditions whole. A list (an aggregator's page) gives
only its new lines, so a hackathon added to it is read once, not the whole list again. A change only in numbers
(a counter, a date of an update) is not one, unless its line speaks of deadlines or applications.

A post ends with the page's links and their texts («Ссылки страницы»), a list's only with those of its new lines:
the model takes the address to apply from them, and a roundup's split gives each announcement its own. They are
kept out of the compared text, where an address's parameters could make a change of nothing.

A site that shows its text only through JavaScript has nothing here (MIN_TEXT): it is refused, and PLAN_Opportunities.md
(«Решения») lists such sites.
"""

import asyncio
import difflib
import hashlib
import re
import time

import httpx
from bs4 import BeautifulSoup

from urllib.parse import urljoin

from my_browser.news.collectors import SHARE, Fetched, Post, clean_url, link_key

PAGE_CHARS = 12000  # a program's conditions fit; the stored text of the page is compared at the next poll
MIN_TEXT = 200
MAX_LINKS = 40
NOISE = ["script", "style", "noscript", "nav", "header", "footer", "form", "svg", "iframe", "button", "aside"]
DIGITS = re.compile(r"\d+")
DEADLINE_WORDS = re.compile(r"deadline|apply|application|registration|due|until|дедлайн|заявк|регистрац|приём|прием|до\s+\d",
                            re.IGNORECASE)


def parse(html: str, base: str = "") -> tuple[str, str, list[tuple[str, str]]]:
    """(title, main text in lines, [(link text, address)]) of a page."""
    soup = BeautifulSoup(html, "html.parser")
    title = " ".join((soup.title.get_text() if soup.title else "").split())
    for tag in soup(NOISE):
        tag.decompose()
    root = soup.find("main") or soup.find("article") or soup.body or soup
    anchors, known = [], set()
    for anchor in root.find_all("a", href=True):
        url = clean_url(urljoin(base, anchor["href"].strip()))
        text = " ".join(anchor.get_text(" ").split())[:100]
        if url.startswith(("http://", "https://")) and text and not SHARE.search(url) and url not in known:
            known.add(url)
            anchors.append((text, url))
    for br in root.find_all("br"):
        br.replace_with("\n")
    lines, seen = [], set()
    for line in root.get_text("\n").splitlines():
        line = " ".join(line.split())
        if line and line not in seen:  # a menu repeated in the body or a card's repeated label adds nothing
            seen.add(line)
            lines.append(line)
    return title, "\n".join(lines)[:PAGE_CHARS], anchors


def main_text(html: str) -> tuple[str, str]:
    """(title, main text in lines) of a page."""
    title, text, _ = parse(html)
    return title, text


def changed_lines(old: str, new: str) -> list[str]:
    """The new text's lines that were not there before; a line that differs from a removed one only in numbers is
    left out, unless it speaks of deadlines or applications."""
    old_lines, new_lines = old.splitlines(), new.splitlines()
    added, removed = [], set()
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, old_lines, new_lines, autojunk=False).get_opcodes():
        if tag in ("replace", "delete"):
            removed.update(DIGITS.sub("#", line) for line in old_lines[i1:i2])
        if tag in ("replace", "insert"):
            added.extend(new_lines[j1:j2])
    return [line for line in added if DIGITS.sub("#", line) not in removed or DEADLINE_WORDS.search(line)]


def fingerprint(text: str) -> str:
    return hashlib.sha1(text.encode()).hexdigest()


async def fetch(source: dict, client: httpx.AsyncClient) -> Fetched:
    response = await client.get(source["url"])
    response.raise_for_status()
    title, text, anchors = await asyncio.to_thread(parse, response.text, str(response.url))
    if len(text) < MIN_TEXT:
        raise ValueError("на странице нет текста без JavaScript")
    page_hash = fingerprint(text)
    if page_hash == source.get("page_hash"):
        return Fetched([], page_hash=page_hash, page_text=text)
    previous = source.get("page_text") or ""
    listing = source.get("category") == "aggregator"
    if previous:
        lines = changed_lines(previous, text)
        if not lines:  # only numbers moved: remember the text, store nothing
            return Fetched([], page_hash=page_hash, page_text=text)
        body = "\n".join(lines) if listing else f"{text}\n\nИзменилось на странице:\n" + "\n".join(lines)
        if listing:  # a list's new lines carry only their own links
            anchors = [(t, u) for t, u in anchors if any(t in line for line in lines)]
    else:
        body = text
    anchors = anchors[:MAX_LINKS]
    if anchors:
        body = body[:PAGE_CHARS] + "\n\nСсылки страницы:\n" + "\n".join(f"{t} — {u}" for t, u in anchors)
    url = clean_url(str(response.url))
    if len(title) <= 12:  # «Home», «Главная», «Competitions»: the source's name says more
        title = source.get("name") or title
    links = list(dict.fromkeys(link_key(u) for _, u in anchors))[:30]
    post = Post(f"{url}#{page_hash[:12]}", url, title or url, body, links, time.time(), digest=listing)  # dated now:
    # the first poll's text is the conditions as they are, not old news to keep apart
    return Fetched([post], page_hash=page_hash, page_text=text)

"""RSS and Atom feeds, with conditional GET: a feed that has not changed answers 304 and costs nothing to parse."""

import asyncio
import calendar
import hashlib

import feedparser
import httpx

from my_browser.news.collectors import Fetched, Post, clean_url, host, text_and_links


async def fetch(source: dict, client: httpx.AsyncClient) -> Fetched:
    headers = {}
    if source.get("etag"):
        headers["If-None-Match"] = source["etag"]
    if source.get("modified"):
        headers["If-Modified-Since"] = source["modified"]
    response = await client.get(source["url"], headers=headers)
    if response.status_code == 304:
        return Fetched([])
    response.raise_for_status()
    posts = await asyncio.to_thread(_parse, response.content, source.get("lane") == "opps")  # OpenAI's 1 300: a second or two
    return Fetched(posts, response.headers.get("etag"), response.headers.get("last-modified"))


def _parse(content: bytes, inline_links: bool = False) -> list[Post]:
    feed = feedparser.parse(content)
    if not feed.entries and feed.bozo:
        raise ValueError(f"not a feed ({type(feed.bozo_exception).__name__})")
    return [post for post in (_post(entry, inline_links) for entry in feed.entries) if post]


def _post(entry, inline_links: bool = False) -> Post | None:
    url = clean_url(entry["link"]) if entry.get("link") else ""
    title = " ".join((entry.get("title") or "").split())
    bodies = [part.get("value") or "" for part in entry.get("content") or []] + [entry.get("summary") or ""]
    text, links = text_and_links(max(bodies, key=len), host(url), inline_links)
    if not title and not text:
        return None
    key = entry.get("id") or entry.get("link") or hashlib.sha1(f"{title}|{text[:200]}".encode()).hexdigest()
    return Post(key[:500], url, title[:500], text, links, _published(entry))


def _published(entry) -> float | None:
    parsed = entry.get("published_parsed") or entry.get("updated_parsed")
    return float(calendar.timegm(parsed)) if parsed else None

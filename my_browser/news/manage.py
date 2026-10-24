"""Sources the user adds and removes (the bot's /add and /remove): a feed's address or a Telegram channel.

A new source is checked before it is stored: a feed must parse and have posts, a channel must have a public preview
with posts. Its owner is the feed's site (tg:<channel> for a channel), so it counts as independent unless that site
is already among the sources. It is polled at the next tick. Removing only switches a source off: its posts stay in
their stories, and a starting source switched off is not offered again.
"""

import asyncio
import re
import time

import feedparser
import httpx

from my_browser.news import db, items, loop, sources
from my_browser.news.collectors import host, rss, telegram

CHANNEL = re.compile(r"^(?:@|https?://t\.me/(?:s/)?)([A-Za-z0-9_]{4,64})/?$")


class SourceError(ValueError):
    """A refused source; the message is for the user, in Russian."""


async def add(text: str) -> dict:
    """Check and store a source; an existing one switched off is switched back on. The stored source."""
    text = text.strip()
    async with httpx.AsyncClient(headers={"User-Agent": loop.USER_AGENT}, timeout=loop.TIMEOUT, follow_redirects=True) as client:
        if match := CHANNEL.match(text):
            name = match.group(1)
            try:
                fetched = await telegram.fetch({"url": name}, client)
            except Exception:
                raise SourceError(f"Канал @{name} не открывается в веб-превью t.me/s: он закрытый или его нет.") from None
            if not fetched.posts:
                raise SourceError(f"У канала @{name} в превью нет постов с текстом.")
            kind, url, title, owner = "telegram", name, f"Telegram: @{name}", f"tg:{name.lower()}"
        elif text.startswith(("http://", "https://")):
            try:
                response = await client.get(text)
                response.raise_for_status()
                feed = await asyncio.to_thread(feedparser.parse, response.content)
                posts = await asyncio.to_thread(rss._parse, response.content)
            except Exception:
                raise SourceError("По этому адресу нет RSS- или Atom-ленты. Нужен адрес самой ленты, а не сайта.") from None
            if not posts:
                raise SourceError("Лента пустая: в ней нет постов.")
            kind, url, owner = "rss", text, host(feed.feed.get("link") or text) or host(text)
            title = " ".join((feed.feed.get("title") or host(text)).split())[:80]
            fetched = None
        else:
            raise SourceError("Пришлите адрес RSS-ленты (https://…/feed) или канал Telegram (@name).")
    lang = items.language(" ".join(p.title for p in (fetched.posts if fetched else posts)[:10]))
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO sources (kind, url, name, category, lang, owner, interval, added) VALUES (?, ?, ?, 'media', ?, ?, ?, ?) "
            "ON CONFLICT (kind, url) DO UPDATE SET enabled = 1", (kind, url, title, lang, owner, sources.INTERVALS["media"], time.time()))
        return dict(conn.execute("SELECT * FROM sources WHERE kind = ? AND url = ?", (kind, url)).fetchone())


def find(text: str) -> list[dict]:
    """Sources matching a number, an address or a part of the name."""
    text = text.strip().removeprefix("@")
    with db.connect() as conn:
        if text.isdigit():
            rows = conn.execute("SELECT * FROM sources WHERE id = ?", (int(text),)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM sources WHERE url = ? OR lower(name) LIKE ? ORDER BY name",
                                (text, f"%{text.lower()}%")).fetchall()
    return [dict(row) for row in rows]


def switch(source_id: int, enabled: bool) -> dict | None:
    with db.transaction() as conn:
        conn.execute("UPDATE sources SET enabled = ? WHERE id = ?", (int(enabled), source_id))
        row = conn.execute("SELECT * FROM sources WHERE id = ?", (source_id,)).fetchone()
    return dict(row) if row else None

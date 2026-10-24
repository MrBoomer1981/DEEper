"""Public Telegram channels through their web preview, t.me/s/<channel>: the last ~20 posts, no account needed.

The page is HTML made for people; the selectors below are what it used on 24.10.2026. A post has no title, so its
first line stands in. A forwarded post links to the original, and a link preview to the page it shows, so the same
news in several channels shares a link_key() at stage 4.
"""

import asyncio
import re
from datetime import datetime

import httpx
from bs4 import BeautifulSoup

from my_browser.news.collectors import SHARE, TEXT_CHARS, Fetched, Post, link_key

TITLE_CHARS = 160
WORDS = re.compile(r"\w{2,}")
DIGEST_LINKS = 4  # a channel's roundup links every story it retells; one story rarely needs four links


async def fetch(source: dict, client: httpx.AsyncClient) -> Fetched:
    name = source["url"].removeprefix("@")
    response = await client.get(f"https://t.me/s/{name}")
    response.raise_for_status()
    posts = await asyncio.to_thread(_parse, response.text, name)
    if not posts and "tgme_widget_message" not in response.text:
        raise ValueError("no public preview")  # t.me/s redirects to the channel's card when the preview is off
    return Fetched(posts)


def _parse(html: str, name: str) -> list[Post]:
    soup = BeautifulSoup(html, "html.parser")
    posts = []
    for message in soup.select("div.tgme_widget_message[data-post]"):
        body = message.select_one(".tgme_widget_message_text")
        if body is None:  # a photo or a video without a caption
            continue
        for br in body.find_all("br"):
            br.replace_with("\n")
        raw = body.get_text("")  # inline links and bold stay inside their line
        lines = [line.strip() for line in raw.splitlines() if line.strip()]
        text = " ".join(raw.split())[:TEXT_CHARS]
        if not text:
            continue
        title = next((line for line in lines if len(WORDS.findall(line)) >= 2), lines[0])  # not a lone «✔️»
        links = []
        anchors = body.select("a[href]") + message.select("a.tgme_widget_message_link_preview[href]")
        anchors += message.select("a.tgme_widget_message_forwarded_from_name[href]")
        for anchor in anchors:
            href = anchor["href"]
            if not href.startswith(("http://", "https://")) or SHARE.search(href):
                continue
            key = link_key(href)
            if not key.lower().startswith(f"t.me/{name.lower()}") and key not in links:
                links.append(key)
        stamp = message.select_one(".tgme_widget_message_date time[datetime]")
        published = datetime.fromisoformat(stamp["datetime"]).timestamp() if stamp else None
        post_id = message["data-post"]  # "channel/123"
        posts.append(Post(post_id, f"https://t.me/{post_id}", title[:TITLE_CHARS], text, links[:30], published,
                          digest=len(links) >= DIGEST_LINKS))
    return posts

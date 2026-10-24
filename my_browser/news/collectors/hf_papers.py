"""Hugging Face Daily Papers through its official API: the papers people upvoted, each linked to arXiv.

The list holds about two weeks of papers; only those with MIN_UPVOTES or more are kept: 42 of 100 had 20 or
more on 24.10.2026, about three a day. A paper that crosses the bar after the dedup window is stored as backfill,
not as news. The whole arXiv cs.CL feed was left out: 183 papers a day, and the notable ones reach this list.
"""

from datetime import datetime

import httpx

from my_browser.news.collectors import TEXT_CHARS, Fetched, Post, link_key

MIN_UPVOTES = 20


async def fetch(source: dict, client: httpx.AsyncClient) -> Fetched:
    response = await client.get(source["url"])
    response.raise_for_status()
    posts = []
    for entry in response.json():
        paper = entry.get("paper") or {}
        arxiv_id = paper.get("id")
        if not arxiv_id or (paper.get("upvotes") or 0) < MIN_UPVOTES:
            continue
        links = [link_key(f"https://huggingface.co/papers/{arxiv_id}")]
        links += [link_key(url) for url in (paper.get("githubRepo"), paper.get("projectPage")) if url]
        title = " ".join((paper.get("title") or entry.get("title") or "").split())
        text = " ".join((paper.get("summary") or paper.get("ai_summary") or "").split())[:TEXT_CHARS]
        posts.append(Post(arxiv_id, f"https://arxiv.org/abs/{arxiv_id}", title, text, links,
                          _published(paper.get("submittedOnDailyAt") or entry.get("publishedAt"))))
    return Fetched(posts)


def _published(value: str | None) -> float | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None

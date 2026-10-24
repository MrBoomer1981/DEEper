"""Storing posts: one row per post and source, never twice, old posts kept apart as backfill.

A source's first poll brings its whole history (OpenAI's feed lists 1 300 posts). Posts older than the dedup
window, and undated posts of the first poll, are stored as backfill: known, so they never come back as new, and
never news. Later, an undated post counts from when it was first seen.
"""

import json
import re
import time

from my_browser.news import db
from my_browser.news.collectors import Post

# A general feed (Techmeme, NVIDIA, 3DNews) keeps only posts that name AI. Acronyms match in capitals only.
ACRONYMS = re.compile(r"\b(AI|ИИ|LLMs?|AGI|GPT[-\w.]*|xAI)\b")
WORDS = re.compile(
    r"artificial intelligence|machine learning|neural net|language models?\b|chatbot|generative|\bagentic\b|"
    r"\b(ChatGPT|OpenAI|Anthropic|Claude|Gemini|DeepMind|Copilot|Llama|Mistral|DeepSeek|Qwen|Grok|Hugging ?Face|"
    r"Perplexity|Midjourney|Sora|GigaChat|YandexGPT|Kandinsky|Алис[аеуы] AI)\b|"
    r"искусственн\w* интеллект|нейросет|нейронн\w* сет|машинн\w* обучени|языков\w* модел|чат-?бот|генеративн",
    re.IGNORECASE)
INSERT = ("INSERT OR IGNORE INTO items (source_id, external_id, url, title, text, links, published, seen, lang, backfill, "
          "digest) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")


def is_about_ai(text: str) -> bool:
    return bool(ACRONYMS.search(text) or WORDS.search(text))


def language(text: str) -> str:
    letters = [c for c in text.lower() if c.isalpha()]
    cyrillic = sum("а" <= c <= "я" or c == "ё" for c in letters)
    return "ru" if letters and cyrillic / len(letters) > 0.3 else "en"


def store(source: dict, posts: list[Post], window_hours: int) -> tuple[int, int]:
    """Insert the posts not stored before; returns (fresh, inserted): fresh ones are news, the rest backfill."""
    now = time.time()
    first_poll = source["last_ok"] is None
    fresh_rows, old_rows = [], []
    for post in posts:
        if source["ai_filter"] and not is_about_ai(f"{post.title} {post.text[:2000]}"):
            continue
        published = min(post.published, now) if post.published else None
        backfill = now - published > window_hours * 3600 if published else first_poll
        row = (source["id"], post.external_id, post.url, post.title, post.text[:300] if backfill else post.text,
               json.dumps(post.links), published or now, now, language(f"{post.title} {post.text[:500]}"), int(backfill),
               int(post.digest))
        (old_rows if backfill else fresh_rows).append(row)
    with db.transaction() as conn:
        start = conn.total_changes
        conn.executemany(INSERT, fresh_rows)
        fresh = conn.total_changes - start
        conn.executemany(INSERT, old_rows)
        inserted = conn.total_changes - start
    return fresh, inserted


def slim(days: int = 30) -> int:
    """Posts older than `days` lose their text and embedding. The rows stay: a feed that still lists such a post
    must not bring it back as new."""
    with db.transaction() as conn:
        cursor = conn.execute("UPDATE items SET text = NULL, embedding = NULL WHERE seen < ? AND text IS NOT NULL",
                              (time.time() - days * 86400,))
        return cursor.rowcount


def recent(limit: int = 50) -> list[dict]:
    """The latest fresh posts with their source, newest first (for checks and, later, the app)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT i.id, i.url, i.title, i.published, i.lang, s.name AS source, s.owner, s.is_primary "
            "FROM items i JOIN sources s ON s.id = i.source_id WHERE i.backfill = 0 ORDER BY i.published DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(row) for row in rows]

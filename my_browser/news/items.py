"""Storing posts: one row per post and source, never twice, old posts kept apart as backfill.

A source's first poll brings its whole history (OpenAI's feed lists 1 300 posts). Posts older than the dedup
window, and undated posts of the first poll, are stored as backfill: known, so they never come back as new, and
never news. Later, an undated post counts from when it was first seen. A post of the opportunities lane stays fresh
for OPPS_FRESH_DAYS: the app shows what is due within three months, so an announcement of two months ago may well be
open. When that window grows, the posts it now takes in were stored as backfill with their text cut: a poll that
lists such a post again stores it whole, as fresh (REVIVE), and widen_opps_window() polls the lane's sources again
in full once.
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
          "digest, lane) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)")
# The opportunities lane: a backfill post that is fresh now (the window grew) comes back whole, once.
REVIVE = (INSERT.replace("INSERT OR IGNORE", "INSERT") + " ON CONFLICT (source_id, external_id) DO UPDATE SET "
          "text = excluded.text, backfill = 0 WHERE items.backfill = 1")
OPPS_FRESH_DAYS = 90  # 30 until 26.10.2026, when the app's window became three months
KEEP_TEXT_DAYS = {"news": 30, "opps": 180}  # an opportunity's posts are read again while it is open


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
    lane = source.get("lane") or "news"
    if lane == "opps":
        window_hours = OPPS_FRESH_DAYS * 24
    fresh_rows, old_rows = [], []
    for post in posts:
        if source["ai_filter"] and not is_about_ai(f"{post.title} {post.text[:2000]}"):
            continue
        published = min(post.published, now) if post.published else None
        backfill = now - published > window_hours * 3600 if published else first_poll
        row = (source["id"], post.external_id, post.url, post.title, post.text[:300] if backfill else post.text,
               json.dumps(post.links), published or now, now, language(f"{post.title} {post.text[:500]}"), int(backfill),
               int(post.digest), lane)
        (old_rows if backfill else fresh_rows).append(row)
    with db.transaction() as conn:
        start = conn.total_changes
        conn.executemany(REVIVE if lane == "opps" else INSERT, fresh_rows)
        fresh = conn.total_changes - start
        conn.executemany(INSERT, old_rows)
        inserted = conn.total_changes - start
    return fresh, inserted


DIGEST_RULE = 2  # 26.10.2026: a channel's footer no longer makes a post a roundup (collectors.telegram.is_digest())


def refresh_digests() -> int:
    """Once after the roundup rule changed: the opportunities lane's Telegram posts taken for roundups are judged
    again, their links normalized anew (VK's redirects, escaped hosts): not cut yet, or cut in the last two days into
    nothing (the cutting model leaves out a «roundup» of one announcement it did not take for an opportunity). Those
    that are single announcements are merged and read as such. How many."""
    if (db.get_state("digest_rule") or 1) >= DIGEST_RULE:
        return 0
    from my_browser.news.collectors import link_key, telegram

    with db.transaction() as conn:
        rows = conn.execute(
            "SELECT i.id, i.links FROM items i JOIN sources s ON s.id = i.source_id WHERE i.lane = 'opps' AND i.digest = 1 "
            "AND s.kind = 'telegram' AND (i.split_at IS NULL OR (i.split_at > ? AND NOT EXISTS "
            "(SELECT 1 FROM items part WHERE part.copied_from = i.id)))", (time.time() - 2 * 86400,)).fetchall()
        single = []
        for row in rows:
            keys = list(dict.fromkeys(link_key(f"https://{key}") for key in json.loads(row["links"] or "[]")))
            if not telegram.is_digest(keys):
                single.append((json.dumps(keys), row["id"]))
        conn.executemany("UPDATE items SET digest = 0, split_at = NULL, links = ? WHERE id = ?", single)
    db.set_state("digest_rule", DIGEST_RULE)
    return len(single)


def widen_opps_window() -> int:
    """Once after OPPS_FRESH_DAYS grew: the lane's sources are polled again at the next tick, their feeds without the
    conditional GET (an unchanged feed answers 304, with no posts), so the posts the window now takes in come back
    whole (REVIVE) and get read. How many sources; 0 when the window is as wide as last time."""
    if (db.get_state("opps_fresh_days") or 30) >= OPPS_FRESH_DAYS:
        return 0
    with db.transaction() as conn:  # a database that never polled the lane: its first polls take the window anyway
        count = conn.execute("UPDATE sources SET etag = NULL, modified = NULL, polled = NULL "
                             "WHERE lane = 'opps' AND enabled = 1 AND last_ok IS NOT NULL").rowcount
    db.set_state("opps_fresh_days", OPPS_FRESH_DAYS)
    return count


def slim() -> int:
    """Posts older than their lane's KEEP_TEXT_DAYS lose their text and embedding. The rows stay: a feed that still
    lists such a post must not bring it back as new."""
    slimmed = 0
    with db.transaction() as conn:
        for lane, days in KEEP_TEXT_DAYS.items():
            slimmed += conn.execute("UPDATE items SET text = NULL, embedding = NULL WHERE lane = ? AND seen < ? "
                                    "AND text IS NOT NULL", (lane, time.time() - days * 86400)).rowcount
    return slimmed


def recent(limit: int = 50) -> list[dict]:
    """The latest fresh posts with their source, newest first (for checks and, later, the app)."""
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT i.id, i.url, i.title, i.published, i.lang, i.lane, s.name AS source, s.owner, s.is_primary "
            "FROM items i JOIN sources s ON s.id = i.source_id WHERE i.backfill = 0 ORDER BY i.published DESC LIMIT ?",
            (limit,)).fetchall()
    return [dict(row) for row in rows]

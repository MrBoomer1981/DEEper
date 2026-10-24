"""Merging posts into stories: one event from many sources becomes one story.

Every few minutes the loop takes the fresh posts without a story (digests aside), embeds them in a child process
(embed.py) and, oldest first, puts each into the story it matches or opens a new one:
- a usable link (collectors.usable_key) shared with a post of an open story means the same story, as long as the
  two are not unrelated (LINK_FLOOR): a page that many posts link to would otherwise pull them all together;
- otherwise the open story whose first post is as close as the threshold (settings.dedup_threshold, chosen by
  eval/news/run_dedup.py), or whose closest post is CHAIN_MARGIN closer still. Measuring from the first post
  keeps a story from drifting: on 24.10.2026 four general «AI in business» pieces chained into one «story», each
  close enough only to the one before it. A story that already has a post of the same owner takes another of its
  posts by meaning only when it is that much closer too: an outlet's different articles on one theme are alike,
  while its second post on one event (a detail, a follow-up) is very close to the first.
When the model cannot run (not downloaded, out of memory), posts are merged by shared links only and stored
without an embedding, so stories and digests go on. A story is open while it got a post within the dedup window. It counts its independent sources (distinct owners)
and whether a first-hand source is among them; stage 5 scores it and writes its Russian title and summary.
"""

import json
import logging
import time
from collections import Counter

import numpy as np

from my_browser.news import config, db, embed
from my_browser.news.collectors import link_key, usable_key

logger = logging.getLogger(__name__)

BATCH = 400  # posts embedded per run; the first run after a deploy takes the window's backlog
LINK_FLOOR = 0.35  # the least similarity for a merge by a shared link; true pairs in the eval had 0.48 and more
CHAIN_MARGIN = 0.08
RANK = {"lab": 0, "papers": 1, "media": 2, "analyst": 3, "community": 4}  # whose title a story shows until stage 5


def _keys(url: str, links: str | None) -> set[str]:
    return {k for k in {link_key(url) if url else ""} | set(json.loads(links or "[]")) if k and usable_key(k)}


def run() -> dict:
    """Merge the waiting posts; returns how many posts were placed and how many stories they opened."""
    settings = config.load()
    now, window = time.time(), settings.dedup_window_hours * 3600
    with db.connect() as conn:
        waiting = [dict(row) for row in conn.execute(
            "SELECT i.id, i.title, i.text, i.url, i.links, s.owner FROM items i JOIN sources s ON s.id = i.source_id "
            "WHERE i.story_id IS NULL AND i.backfill = 0 AND i.digest = 0 AND i.text IS NOT NULL AND i.published > ? "
            "ORDER BY i.published LIMIT ?", (now - window, BATCH))]
        if not waiting:
            return {"posts": 0, "stories": 0}
        placed = [dict(row) for row in conn.execute(
            "SELECT i.story_id, i.url, i.links, i.embedding, src.owner FROM items i JOIN stories s ON s.id = i.story_id "
            "JOIN sources src ON src.id = i.source_id WHERE s.last_updated > ? AND i.embedding IS NOT NULL "
            "ORDER BY i.published", (now - window,))]
    try:
        vectors = embed.vectors([embed.text(p["title"], p["text"]) for p in waiting])
    except Exception as e:
        logger.warning("News: embeddings unavailable (%s); merging by shared links only", str(e)[:300])
        vectors = None

    index_vectors = [np.frombuffer(p["embedding"], dtype=np.float32) for p in placed]
    index_story = [p["story_id"] for p in placed]
    seeds: dict[int, np.ndarray] = {}  # each story's first post
    owners: dict[int, set[str]] = {}
    for vector, story, p in zip(index_vectors, index_story, placed):
        seeds.setdefault(story, vector)
        owners.setdefault(story, set()).add(p["owner"])
    key_story: dict[str, int] = {}
    for p in placed:
        for key in _keys(p["url"], p["links"]):
            key_story.setdefault(key, p["story_id"])

    assignments: list[tuple[int, int | None, bytes]] = []  # (item, story or None for a new one, embedding)
    new_stories = 0
    for n, post in enumerate(waiting):
        vector = vectors[n] if vectors is not None else None
        similarity = np.vstack(index_vectors) @ vector if vector is not None and index_vectors else None
        story = None
        linked = Counter(key_story[k] for k in _keys(post["url"], post["links"]) if k in key_story)
        for candidate, _ in linked.most_common():
            closest = max((s for s, sid in zip(similarity, index_story) if sid == candidate), default=0.0) \
                if similarity is not None else LINK_FLOOR  # without embeddings a shared link is all there is
            if closest >= LINK_FLOOR:
                story = candidate
                break
        if story is None and similarity is not None:
            same_owner = {sid for sid, names in owners.items() if post["owner"] in names}
            story = _closest(similarity, index_story, seeds, vector, settings.dedup_threshold, same_owner)
        if story is None:
            new_stories += 1
            story = -new_stories  # a placeholder until the story row exists
            if vector is not None:
                seeds[story] = vector
        owners.setdefault(story, set()).add(post["owner"])
        assignments.append((post["id"], story, vector.tobytes() if vector is not None else None))
        if vector is not None:
            index_vectors.append(vector)
            index_story.append(story)
        for key in _keys(post["url"], post["links"]):
            key_story.setdefault(key, story)

    with db.transaction() as conn:
        real: dict[int, int] = {}
        for _, story, _ in assignments:
            if story < 0 and story not in real:
                real[story] = conn.execute("INSERT INTO stories (first_seen, last_updated) VALUES (?, ?)", (now, now)).lastrowid
        touched = set()
        for item, story, vector in assignments:
            story = real.get(story, story)
            touched.add(story)
            conn.execute("UPDATE items SET story_id = ?, embedding = ? WHERE id = ?", (story, vector, item))
        for story in touched:
            refresh(conn, story)
    logger.info("News: %d posts merged into %d stories, %d of them new", len(assignments), len(touched), new_stories)
    return {"posts": len(assignments), "stories": len(touched), "new": new_stories}


def _closest(similarity: np.ndarray, index_story: list[int], seeds: dict, vector: np.ndarray, threshold: float,
             same_owner: set[int]) -> int | None:
    """The story a post joins by meaning: close to its first post, or very close to one of its posts (only the latter
    where the post's owner is already in); the closest such."""
    nearest: dict[int, float] = {}
    for score, story in zip(similarity, index_story):
        if score > nearest.get(story, -1.0):
            nearest[story] = float(score)
    best, best_score = None, 0.0
    for story, score in nearest.items():
        very_close = score >= threshold + CHAIN_MARGIN
        close = score >= threshold and float(seeds[story] @ vector) >= threshold and story not in same_owner
        if (very_close or close) and score > best_score:
            best, best_score = story, score
    return best


def refresh(conn, story: int) -> None:
    """A story's counts and its provisional title (the first-hand post's, else the earliest of the best kind)."""
    rows = conn.execute(
        "SELECT i.title, i.published, i.seen, s.owner, s.is_primary, s.category FROM items i "
        "JOIN sources s ON s.id = i.source_id WHERE i.story_id = ?", (story,)).fetchall()
    best = min(rows, key=lambda r: (not r["is_primary"], RANK.get(r["category"], 9), r["published"]))
    conn.execute(
        "UPDATE stories SET first_seen = ?, last_updated = ?, source_count = ?, title = ?, "
        "has_primary = MAX(?, primary_url IS NOT NULL) WHERE id = ?",  # a first-hand link the model found stays
        (min(r["published"] for r in rows), max(r["seen"] for r in rows), len({r["owner"] for r in rows}), best["title"],
         int(any(r["is_primary"] for r in rows)), story))


def stories(limit: int = 30, hours: int = 48) -> list[dict]:
    """Recent stories, the most independent sources first, with their posts."""
    since = time.time() - hours * 3600
    with db.connect() as conn:
        rows = [dict(row) for row in conn.execute(
            "SELECT * FROM stories WHERE last_updated > ? AND status != 'merged' "
            "ORDER BY source_count DESC, last_updated DESC LIMIT ?",
            (since, limit))]
        for story in rows:
            story["posts"] = [dict(p) for p in conn.execute(
                "SELECT i.title, i.url, i.published, s.name AS source, s.owner, s.is_primary FROM items i "
                "JOIN sources s ON s.id = i.source_id WHERE i.story_id = ? ORDER BY s.is_primary DESC, i.published",
                (story["id"],))]
    return rows

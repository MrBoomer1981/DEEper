"""Learning from 👍/👎: corrections to topics and sources that raise or lower a story's importance.

Feedback of the last LEARN_DAYS days is summed per topic and per source owner: each 👍 adds STEP, each 👎 takes it
away, capped at ±MAX_SHIFT importance points. A story's effective importance is its scored importance plus its
topic's correction plus the mean correction of its owners; digests, urgent messages and «Радар» order and filter by
it (delivery.py). The corrections are computed from the feedback each time, never written into the settings or the
sources: what the user set by hand stays as it is, and old feedback fades out after LEARN_DAYS.
"""

import time
from collections import Counter

from my_browser.news import db

LEARN_DAYS = 30
STEP = 0.25  # four 👎 on a topic lower it a point; at 0.5 they took a topic out of the digest altogether
MAX_SHIFT = 2.0


def record(story_id: int, value: int) -> None:
    with db.transaction() as conn:
        conn.execute("INSERT INTO feedback (story_id, value, created) VALUES (?, ?, ?)", (story_id, 1 if value > 0 else -1, time.time()))


def _owners(conn, story_id: int) -> list[str]:
    return [row[0] for row in conn.execute(
        "SELECT DISTINCT s.owner FROM items i JOIN sources s ON s.id = i.source_id WHERE i.story_id = ?", (story_id,))]


def corrections(conn) -> tuple[dict[str, float], dict[str, float]]:
    """(per topic, per owner) importance corrections from the recent feedback."""
    topics, owners = Counter(), Counter()
    for row in conn.execute("SELECT f.value, s.id, s.topic FROM feedback f JOIN stories s ON s.id = f.story_id WHERE f.created > ?",
                            (time.time() - LEARN_DAYS * 86400,)):
        topics[row["topic"]] += row["value"]
        for owner in _owners(conn, row["id"]):
            owners[owner] += row["value"]

    def shift(total: int) -> float:
        return max(-MAX_SHIFT, min(MAX_SHIFT, total * STEP))

    return {t: shift(v) for t, v in topics.items() if v}, {o: shift(v) for o, v in owners.items() if v}


def rank(conn, stories: list[dict]) -> list[dict]:
    """The stories with `effective` importance set, the most important first (then by independent sources)."""
    by_topic, by_owner = corrections(conn)
    for story in stories:
        owners = _owners(conn, story["id"]) if by_owner else []
        owner_shift = sum(by_owner.get(o, 0.0) for o in owners) / len(owners) if owners else 0.0
        story["effective"] = round((story["importance"] or 0) + by_topic.get(story["topic"], 0.0) + owner_shift, 2)
    return sorted(stories, key=lambda s: (-s["effective"], -(s["source_count"] or 0), -(s["last_updated"] or 0)))

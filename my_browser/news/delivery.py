"""Delivery: what goes to Telegram and when. The app's «Радар» reads stories itself.

The news part decides, the bot sends: messages wait in the deliveries table; the bot takes them (GET /deliveries),
sends them to TELEGRAM_ALLOWED_USERS and marks them sent (POST /deliveries/{id}/sent).
- Urgent: a queued story from urgent_min_importance up, first seen in the last URGENT_HOURS, goes out at once; in
  the quiet hours it waits for their end. Several at once go in one message. Right after the quiet hours, when a
  digest is due within DIGEST_HOLD_HOURS (08:00 and 09:00 by default), the night's urgent stories wait for it and
  lead it: one morning message instead of two in an hour.
- Digest: at each digest time (within DIGEST_LATE_HOURS of it, so a restart does not lose it), the queued stories
  not sent yet, the most important first, at most digest_max_stories, with numbered buttons to research any.
- A sent story that grew to twice its sources at sending, and UPDATE_MIN more, comes back once in a digest as an
  update; never twice.
Nothing goes out while paused, and a story is never sent twice. Only the topics on in the settings go out
(and into «Радар»). Everything is ordered and filtered by the effective importance: the scored one plus what 👍/👎
taught (feedback.py). A dropped story the user brings back («Это было важно», /dropped) goes out at once.
"""

import html
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from my_browser.common.text import plural
from my_browser.news import config, db, feedback

URGENT_HOURS = 12
DIGEST_LATE_HOURS = 2
DIGEST_HOLD_HOURS = 2
UPDATE_MIN = 3
DIGEST_MIN_IMPORTANCE = 4
KEEP_DELIVERIES_DAYS = 14


def _topics(settings: config.Settings) -> tuple[str, list[str]]:
    """The SQL condition for the topics on, and its parameters."""
    return f"topic IN ({','.join('?' * len(settings.topics))})" if settings.topics else "0", list(settings.topics)


def _local_now(settings: config.Settings) -> datetime:
    return datetime.now(ZoneInfo(settings.timezone))


def quiet(settings: config.Settings, moment: datetime) -> bool:
    """Whether a local moment falls into the quiet hours (they may wrap past midnight; equal times: none)."""
    start, end, now = settings.quiet_start, settings.quiet_end, moment.strftime("%H:%M")
    if start == end:
        return False
    return start <= now < end if start < end else (now >= start or now < end)


def _posts(conn, story_id: int) -> list[dict]:
    return [dict(row) for row in conn.execute(
        "SELECT i.title, i.url, s.name AS source, s.is_primary FROM items i JOIN sources s ON s.id = i.source_id "
        "WHERE i.story_id = ? ORDER BY s.is_primary DESC, i.published", (story_id,))]


def header(story: dict, marks: bool = True) -> str:
    """«🟢 первоисточник · 7 источников · модели»: whether a first-hand source is in, how many independent ones.
    The app takes it without the colored marks: green and yellow are not in its brand's palette."""
    count = story["source_count"] or 1
    mark = "первоисточник" if story["has_primary"] else "пересказы"
    if marks:
        mark = ("🟢 " if story["has_primary"] else "🟡 ") + mark
    return f"{mark} · {count} {plural(count, 'источник', 'источника', 'источников')} · {config.TOPICS.get(story['topic'], 'ИИ')}"


def story_html(story: dict, posts: list[dict], prefix: str = "") -> str:
    """One story for Telegram (HTML): header, title, summary and the link to its best source."""
    first_hand = story["primary_url"] or next((p["url"] for p in posts if p["is_primary"]), None)
    link, label = (first_hand, "первоисточник") if first_hand else (posts[0]["url"], posts[0]["source"]) if posts else ("", "")
    more = (story["source_count"] or 1) - 1
    lines = [f"{prefix}{header(story)}", f"<b>{html.escape(story['title_ru'] or story['title'] or '')}</b>"]
    if story["summary_ru"]:
        lines.append(html.escape(story["summary_ru"]))
    if link:
        lines.append(f'🔗 <a href="{html.escape(link, quote=True)}">{html.escape(label)}</a>' + (f" · ещё {more}" if more > 0 else ""))
    return "\n".join(lines)


def _enqueue(conn, kind: str, text: str, buttons: list[list[list[str]]], story_ids: list[int]) -> None:
    conn.execute("INSERT INTO deliveries (kind, created, text, buttons, story_ids) VALUES (?, ?, ?, ?, ?)",
                 (kind, time.time(), text, json.dumps(buttons, ensure_ascii=False), json.dumps(story_ids)))


def run() -> dict:
    """Queue what is due now: urgent stories and digests. A summary of what was queued."""
    settings = config.load()
    if settings.paused:
        return {"urgent": 0, "digest": 0}
    now, local = time.time(), _local_now(settings)
    queued = {"urgent": 0, "digest": 0}
    slot = _due_digest(settings, local)
    with db.transaction() as conn:
        if slot:  # first: a digest due now takes the urgent stories waiting, the most important lead it
            queued["digest"] = _digest(conn, settings, slot)
        if settings.urgent_enabled and not quiet(settings, local) and not _held_for_digest(settings, local):
            on, topics = _topics(settings)
            urgent = [s for s in feedback.rank(conn, [dict(row) for row in conn.execute(
                f"SELECT * FROM stories WHERE status = 'queued' AND importance >= ? AND first_seen > ? AND summary_ru IS NOT NULL "
                f"AND {on}", [settings.urgent_min_importance - feedback.MAX_SHIFT, now - URGENT_HOURS * 3600, *topics])])
                      if s["effective"] >= settings.urgent_min_importance]
            if urgent:
                _urgent(conn, urgent)
                queued["urgent"] = len(urgent)
        conn.execute("DELETE FROM deliveries WHERE created < ?", (now - KEEP_DELIVERIES_DAYS * 86400,))
    if slot:  # after the transaction: set_state takes the same write lock
        db.set_state("digests_done", (db.get_state("digests_done", []) + [slot])[-20:])
    return queued


def _held_for_digest(settings: config.Settings, local: datetime) -> bool:
    """Whether the quiet hours ended less than DIGEST_HOLD_HOURS ago and a digest comes before that span is over."""
    if settings.quiet_start == settings.quiet_end:
        return False
    hours, minutes = map(int, settings.quiet_end.split(":"))
    end = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    until = end + timedelta(hours=DIGEST_HOLD_HOURS)
    if not end <= local < until:
        return False
    for time_text in settings.digest_times:
        hours, minutes = map(int, time_text.split(":"))
        if local < local.replace(hour=hours, minute=minutes, second=0, microsecond=0) <= until:
            return True
    return False


def _urgent(conn, stories: list[dict]) -> None:
    blocks = [story_html(s, _posts(conn, s["id"])) for s in stories]
    if len(stories) == 1:
        story = stories[0]
        text = "⚡️ <b>Срочное</b>\n\n" + blocks[0]
        buttons = [[["🔎 Исследовать", f"nr:{story['id']}"], ["👍", f"nf:{story['id']}:1"], ["👎", f"nf:{story['id']}:-1"]]]
    else:
        text = "⚡️ <b>Срочное</b>\n\n" + "\n\n".join(f"{n}. {block}" for n, block in enumerate(blocks, 1))
        buttons = _number_buttons([s["id"] for s in stories])
    _enqueue(conn, "urgent", text, buttons, [s["id"] for s in stories])
    _mark_sent(conn, stories)


def _due_digest(settings: config.Settings, local: datetime) -> str | None:
    """The digest slot («2026-10-25 09:00») due now and not made yet, if any."""
    done = set(db.get_state("digests_done", []))
    for time_text in settings.digest_times:
        hours, minutes = map(int, time_text.split(":"))
        slot = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
        key = f"{slot:%Y-%m-%d} {time_text}"
        if slot <= local < slot + timedelta(hours=DIGEST_LATE_HOURS) and key not in done:
            return key
    return None


def _digest(conn, settings: config.Settings, slot: str) -> int:
    on, topics = _topics(settings)
    fresh = [s for s in feedback.rank(conn, [dict(row) for row in conn.execute(
        f"SELECT * FROM stories WHERE status = 'queued' AND importance >= ? AND last_updated > ? AND {on}",
        [DIGEST_MIN_IMPORTANCE - feedback.MAX_SHIFT, time.time() - settings.dedup_window_hours * 3600, *topics])])
             if s["effective"] >= DIGEST_MIN_IMPORTANCE][:settings.digest_max_stories]
    room = settings.digest_max_stories - len(fresh)
    grown = [dict(row) for row in conn.execute(
        "SELECT * FROM stories WHERE status = 'sent' AND update_sent = 0 AND sent_count IS NOT NULL "
        "AND source_count >= MAX(2 * sent_count, sent_count + ?) ORDER BY importance DESC LIMIT ?",
        (UPDATE_MIN, max(room, 0)))]
    stories = fresh + grown
    if not stories:
        return 0
    blocks = [story_html(s, _posts(conn, s["id"]), prefix="🔄 обновление · " if s in grown else "") for s in stories]
    count = len(stories)
    text = (f"📰 <b>Новости ИИ · {slot[-5:]}</b> · {count} {plural(count, 'сюжет', 'сюжета', 'сюжетов')}\n\n"
            + "\n\n".join(f"{n}. {block}" for n, block in enumerate(blocks, 1)))
    _enqueue(conn, "digest", text, _number_buttons([s["id"] for s in stories]), [s["id"] for s in stories])
    _mark_sent(conn, fresh)
    for story in grown:
        conn.execute("UPDATE stories SET update_sent = 1 WHERE id = ?", (story["id"],))
    return count


def _number_buttons(story_ids: list[int]) -> list[list[list[str]]]:
    """«🔎 1» … under a message of several stories: research the story with that number."""
    buttons = [[f"🔎 {n}", f"nr:{sid}"] for n, sid in enumerate(story_ids, 1)]
    return [buttons[i:i + 5] for i in range(0, len(buttons), 5)]


def _mark_sent(conn, stories: list[dict]) -> None:
    for story in stories:
        conn.execute("UPDATE stories SET status = 'sent', sent_at = ?, sent_count = source_count WHERE id = ?",
                     (time.time(), story["id"]))


def pending() -> list[dict]:
    with db.connect() as conn:
        rows = conn.execute("SELECT id, kind, created, text, buttons FROM deliveries WHERE sent_at IS NULL ORDER BY id").fetchall()
    return [{**dict(row), "buttons": json.loads(row["buttons"] or "[]")} for row in rows]


def mark_delivered(delivery_id: int) -> bool:
    with db.transaction() as conn:
        return conn.execute("UPDATE deliveries SET sent_at = ? WHERE id = ? AND sent_at IS NULL",
                            (time.time(), delivery_id)).rowcount > 0


def story(story_id: int) -> dict | None:
    """One story with its posts (the bot's «🔎» reads its title)."""
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM stories WHERE id = ?", (story_id,)).fetchone()
        return {**dict(row), "posts": _posts(conn, story_id)} if row else None


def feed(limit: int = 12, hours: int = 36) -> list[dict]:
    """«Радар»: the scored stories of the last hours, the most important first, each with its header and best link."""
    on, topics = _topics(config.load())
    with db.connect() as conn:
        rows = [s for s in feedback.rank(conn, [dict(row) for row in conn.execute(
            f"SELECT * FROM stories WHERE status IN ('queued', 'sent') AND importance >= ? AND last_updated > ? AND {on}",
            [DIGEST_MIN_IMPORTANCE - feedback.MAX_SHIFT, time.time() - hours * 3600, *topics])])
                if s["effective"] >= DIGEST_MIN_IMPORTANCE][:limit]
        for row in rows:
            posts = _posts(conn, row["id"])
            first_hand = row["primary_url"] or next((p["url"] for p in posts if p["is_primary"]), None)
            row["header"] = header(row, marks=False)
            row["link"], row["link_label"] = (first_hand, "первоисточник") if first_hand else \
                (posts[0]["url"], posts[0]["source"]) if posts else (None, None)
    return rows


def dropped(hours: int = 24, limit: int = 30) -> list[dict]:
    """What scoring dropped in the last hours, the most important first, each with its reason."""
    with db.connect() as conn:
        return [dict(row) for row in conn.execute(
            "SELECT id, title_ru, title, importance, topic, drop_reason, source_count FROM stories "
            "WHERE status = 'dropped' AND scored_at > ? ORDER BY importance DESC, source_count DESC LIMIT ?",
            (time.time() - hours * 3600, limit))]


def restore(story_id: int) -> dict | None:
    """«Это было важно»: a dropped story goes out now in a message of its own, and it counts as 👍."""
    with db.transaction() as conn:
        row = conn.execute("SELECT * FROM stories WHERE id = ? AND status = 'dropped'", (story_id,)).fetchone()
        if row is None:
            return None
        story = {**dict(row), "importance": max(row["importance"] or 0, DIGEST_MIN_IMPORTANCE)}
        conn.execute("UPDATE stories SET importance = ?, drop_reason = NULL WHERE id = ?", (story["importance"], story_id))
        text = "↩️ <b>Вернул отсеянное</b>\n\n" + story_html(story, _posts(conn, story_id))
        _enqueue(conn, "restored", text, [[["🔎 Исследовать", f"nr:{story_id}"]]], [story_id])
        _mark_sent(conn, [story])
    feedback.record(story_id, 1)
    return story

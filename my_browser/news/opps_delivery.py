"""Opportunities to Telegram: the weekly digest, «hot» ones at once, reminders of tracked deadlines.

Like the news (delivery.py), messages wait in the deliveries table and the bot sends them; the cards and their
buttons are written here. Every tick run() queues what is due:
- Hot: a new open opportunity whose deadline is within opps_hot_days and whose fit is at least opps_hot_min_fit goes
  out at once, several together in one message (HOT_MAX at most); in the quiet hours it waits for their end. Without a
  deadline the event's start stands in (effective()): a hackathon starting in five days is as hot.
- The weekly digest, on opps_digest_day at opps_digest_time (within DIGEST_LATE_HOURS, so a restart does not lose it):
  the new open opportunities not sent yet, by how close the deadline is (within two weeks, a month, later, none),
  within that by fit; low fit and no access from Russia go last; at most DIGEST_MAX, the rest counted. Once a month it
  also asks about tracked ones without a date: «ещё открыто?».
- Reminders: a tracked opportunity's deadline opps_reminders days ahead (14, 3, 1 by default), by calendar days in
  the settings' time zone («за 1 день» comes the day before, not the night before at 23:59), each once (opp_reminders);
  when several are due at once (tracked late), one message goes. They wait out the quiet
  hours too. An approximate deadline is said to be one; rolling and unknown ones get none. Applied, hidden or expired
  ones get nothing more.
Only the types on in opps_types go out (all are read). Buttons: «📌 Отслеживать / ✖️ Не моё / 🔎 Исследовать»
(op:<story>:t|h|r) under a single card, «📌 N» under several, «✅ Подал» (op:<story>:a) under a reminder.
"""

import html
import json
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from my_browser.common.text import plural
from my_browser.news import config, db, delivery

HOT_MAX = 10
DIGEST_MAX = 15
DIGEST_LATE_HOURS = 6
ONE = {"hackathon": "Хакатон", "competition": "Соревнование", "school": "Школа", "internship": "Стажировка",
       "fellowship": "Программа", "education": "Поступление", "conference": "Конференция", "grant": "Грант",
       "talk": "Выступление", "exam": "Экзамен", "other": "Возможность"}
ICON = {"hackathon": "🏁", "competition": "🏆", "school": "🎓", "internship": "💼", "fellowship": "🔬", "education": "📚",
        "conference": "📄", "grant": "💰", "talk": "🎤", "exam": "📝", "other": "✨"}
ACCESS = {"yes": "✅ из РФ", "unclear": "⚠️ из РФ — неясно", "no": "⛔ не из РФ"}
FORMATS = {"online": "онлайн", "offline": "офлайн", "hybrid": "гибрид"}
MONTHS = ("янв", "фев", "мар", "апр", "мая", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
WEEKDAY = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _zone(settings: config.Settings) -> ZoneInfo:
    return ZoneInfo(settings.timezone)


def day(value, zone: ZoneInfo) -> str:
    """«15 ноя» for seconds since the epoch or an ISO date (time)."""
    moment = datetime.fromtimestamp(value, zone) if isinstance(value, (int, float)) else datetime.fromisoformat(str(value))
    return f"{moment.day} {MONTHS[moment.month - 1]}"


def effective(opp: dict, zone: ZoneInfo) -> float | None:
    """The deadline, or else the event's start (the morning of its day): what «how soon» is measured by."""
    if opp["deadline_at"]:
        return opp["deadline_at"]
    if opp.get("starts") and opp["deadline_kind"] != "rolling":
        try:
            return datetime.fromisoformat(opp["starts"]).replace(tzinfo=zone).timestamp()
        except ValueError:
            return None
    return None


def when(opp: dict, now: float, zone: ZoneInfo) -> str:
    """«дедлайн 12 ноя (через 18 дн)», «дедлайн ≈ 30 ноя», «приём круглый год», «дедлайн не указан · старт 6 ноя»."""
    if opp["deadline_kind"] == "rolling":
        return "приём круглый год"
    if not opp["deadline_at"]:
        return "дедлайн не указан" + (f" · старт {day(opp['starts'], zone)}" if opp.get("starts") else "")
    moment = datetime.fromtimestamp(opp["deadline_at"], zone)
    days = (moment.date() - datetime.fromtimestamp(now, zone).date()).days
    left = "сегодня" if days == 0 else "завтра" if days == 1 else "прошёл" if days < 0 else \
        f"через {days} {plural(days, 'день', 'дня', 'дней')}"
    mark = "≈ " if opp["deadline_kind"] == "approximate" else ""
    return f"дедлайн {mark}{moment.day} {MONTHS[moment.month - 1]} ({left})"


def card(opp: dict, now: float, zone: ZoneInfo, number: int | None = None) -> str:
    """One opportunity for Telegram (HTML), as the plan draws it."""
    head = [f"{ICON.get(opp['opp_type'], '✨')} {ONE.get(opp['opp_type'], 'Возможность')}", when(opp, now, zone)]
    if opp["format"]:
        head.append(FORMATS.get(opp["format"], opp["format"]))
    head.append(ACCESS.get(opp["ru_access"] or "unclear", ACCESS["unclear"]))
    title = html.escape(opp["title_ru"] or opp.get("title") or "")
    lines = [(f"{number}. " if number else "") + " · ".join(head),
             f"<b>{title}</b>" + (f" — {html.escape(opp['organizer'])}" if opp["organizer"] else "")]
    if opp["summary_ru"]:
        lines.append(html.escape(opp["summary_ru"]))
    if opp["fit_reason"]:
        lines.append(f"Почему тебе: {html.escape(opp['fit_reason'])}")
    link = opp["apply_url"] or opp.get("link")
    if link:
        lines.append(f'🔗 <a href="{html.escape(link, quote=True)}">подача</a>')
    return "\n".join(lines)


def card_buttons(story_id: int) -> list[list[list[str]]]:
    return [[["📌 Отслеживать", f"op:{story_id}:t"], ["✖️ Не моё", f"op:{story_id}:h"], ["🔎 Исследовать", f"op:{story_id}:r"]]]


def number_buttons(story_ids: list[int]) -> list[list[list[str]]]:
    buttons = [[f"📌 {n}", f"op:{sid}:t"] for n, sid in enumerate(story_ids, 1)]
    return [buttons[i:i + 5] for i in range(0, len(buttons), 5)]


def _enqueue(conn, kind: str, text: str, buttons: list, story_ids: list[int]) -> None:
    conn.execute("INSERT INTO deliveries (kind, created, text, buttons, story_ids) VALUES (?, ?, ?, ?, ?)",
                 (kind, time.time(), text, json.dumps(buttons, ensure_ascii=False), json.dumps(story_ids)))


def _types(settings: config.Settings) -> tuple[str, list[str]]:
    marks = ",".join("?" * len(settings.opps_types))
    return (f"o.opp_type IN ({marks})" if settings.opps_types else "0"), list(settings.opps_types)


def _link(conn, story_id: int) -> str | None:
    row = conn.execute("SELECT url FROM items WHERE story_id = ? ORDER BY published DESC LIMIT 1", (story_id,)).fetchone()
    return row["url"] if row else None


def _rows(conn, where: str, params: list) -> list[dict]:
    rows = [dict(row) for row in conn.execute(
        f"SELECT o.*, s.title FROM opportunities o JOIN stories s ON s.id = o.story_id WHERE s.status != 'merged' AND {where}",
        params)]
    for row in rows:
        row["link"] = _link(conn, row["story_id"])
    return rows


def order(opps: list[dict], now: float, zone: ZoneInfo) -> list[dict]:
    """By how soon (effective(): two weeks, a month, later, unknown), within that by fit; low fit (below 3) and no
    access from Russia last."""
    def bucket(opp: dict) -> int:
        at = effective(opp, zone)
        if not at:
            return 3
        days = (at - now) / 86400
        return 0 if days <= 14 else 1 if days <= 31 else 2
    return sorted(opps, key=lambda o: ((o["fit"] or 0) < 3 or o["ru_access"] == "no", bucket(o), -(o["fit"] or 0),
                                      effective(o, zone) or 0))


def run(now: float | None = None, local: datetime | None = None) -> dict:
    """Queue what is due: hot opportunities, the weekly digest, reminders. A summary of what was queued."""
    settings = config.load()
    if not settings.opps_enabled:
        return {"enabled": False}
    now = now or time.time()
    zone = _zone(settings)
    local = local or datetime.fromtimestamp(now, zone)
    queued = {"hot": 0, "digest": 0, "reminders": 0}
    digest_key = _due_digest(settings, local)
    with db.transaction() as conn:
        if digest_key:  # first: a digest due now takes the hot ones of this moment too
            queued["digest"] = _digest(conn, settings, now, zone, local)
        if not delivery.quiet(settings, local):
            queued["hot"] = _hot(conn, settings, now, zone)
            queued["reminders"] = _reminders(conn, settings, now, zone)
    if digest_key:  # after the transaction: set_state takes the same write lock
        db.set_state("opps_digests_done", (db.get_state("opps_digests_done", []) + [digest_key])[-10:])
    return queued


def _hot(conn, settings: config.Settings, now: float, zone: ZoneInfo) -> int:
    on, types = _types(settings)
    soon = now + settings.opps_hot_days * 86400
    hot = order([o for o in _rows(conn, f"o.status = 'open' AND o.sent_at IS NULL AND o.fit >= ? AND {on}",
                                  [settings.opps_hot_min_fit, *types])
                 if (at := effective(o, zone)) and now < at <= soon], now, zone)[:HOT_MAX]
    if not hot:
        return 0
    if len(hot) == 1:
        text = "🔥 <b>Горящая возможность</b>\n\n" + card(hot[0], now, zone)
        buttons = card_buttons(hot[0]["story_id"])
    else:
        text = (f"🔥 <b>Горящие возможности</b> · {len(hot)}\n\n"
                + "\n\n".join(card(o, now, zone, n) for n, o in enumerate(hot, 1)))
        buttons = number_buttons([o["story_id"] for o in hot])
    _enqueue(conn, "opp_hot", text, buttons, [o["story_id"] for o in hot])
    _mark_sent(conn, hot, now)
    return len(hot)


def _due_digest(settings: config.Settings, local: datetime) -> str | None:
    """The digest's date («2026-10-25») when it is due now and not made yet."""
    hours, minutes = map(int, settings.opps_digest_time.split(":"))
    slot = local.replace(hour=hours, minute=minutes, second=0, microsecond=0)
    key = f"{slot:%Y-%m-%d}"
    if local.weekday() != WEEKDAY[settings.opps_digest_day] or not slot <= local < slot + timedelta(hours=DIGEST_LATE_HOURS):
        return None
    return None if key in db.get_state("opps_digests_done", []) else key


def _digest(conn, settings: config.Settings, now: float, zone: ZoneInfo, local: datetime) -> int:
    on, types = _types(settings)
    fresh = order(_rows(conn, f"o.status = 'open' AND o.sent_at IS NULL AND {on}", types), now, zone)
    shown, rest = fresh[:DIGEST_MAX], fresh[DIGEST_MAX:]
    month = f"{local:%Y-%m}"
    check = []
    if db.get_state("opps_check_month") != month:  # once a month: tracked ones without a date, still open?
        check = _rows(conn, "o.status = 'tracked' AND o.deadline_at IS NULL", [])
    if not shown and not check:
        return 0
    lines = [f"🎯 <b>Возможности недели</b> · {len(fresh)} {plural(len(fresh), 'новая', 'новых', 'новых')}"]
    lines += [card(o, now, zone, n) for n, o in enumerate(shown, 1)]
    if rest:
        lines.append(f"…и ещё {len(rest)}: /opps")
    if check:
        lines.append("❓ <b>Ещё открыто?</b> Отслеживаешь без даты — проверь:\n"
                     + "\n".join(f"• {html.escape(o['title_ru'] or o['title'] or '')}" for o in check))
    _enqueue(conn, "opp_digest", "\n\n".join(lines), number_buttons([o["story_id"] for o in shown]),
             [o["story_id"] for o in shown + check])
    _mark_sent(conn, fresh, now)  # the ones counted but not shown are in /opps, not in the next digest
    if check:
        conn.execute("INSERT INTO state (key, value) VALUES ('opps_check_month', ?) ON CONFLICT (key) DO UPDATE SET "
                     "value = excluded.value", (json.dumps(month),))
    return len(shown)


def _reminders(conn, settings: config.Settings, now: float, zone: ZoneInfo) -> int:
    sent = 0
    for opp in _rows(conn, "o.status = 'tracked' AND o.deadline_at > ? AND o.deadline_kind IN ('exact', 'approximate')", [now]):
        done = {row[0] for row in conn.execute("SELECT days_before FROM opp_reminders WHERE story_id = ?", (opp["story_id"],))}
        left = (datetime.fromtimestamp(opp["deadline_at"], zone).date() - datetime.fromtimestamp(now, zone).date()).days
        due = [d for d in settings.opps_reminders if left <= d and d not in done]
        if not due:
            continue
        text = f"⏰ <b>Скоро дедлайн</b>\n\n{card(opp, now, zone)}"
        if opp["deadline_kind"] == "approximate":
            text += "\n⚠️ Точной даты нет — проверь на сайте."
        _enqueue(conn, "opp_reminder", text, [[["✅ Подал", f"op:{opp['story_id']}:a"],
                                               ["✖️ Не буду", f"op:{opp['story_id']}:h"]]], [opp["story_id"]])
        conn.executemany("INSERT OR IGNORE INTO opp_reminders (story_id, days_before, sent_at) VALUES (?, ?, ?)",
                         [(opp["story_id"], d, now) for d in due])  # the farther ones are past already
        sent += 1
    return sent


def _mark_sent(conn, opps: list[dict], now: float) -> None:
    conn.executemany("UPDATE opportunities SET sent_at = ? WHERE story_id = ?", [(now, o["story_id"]) for o in opps])


def opportunity(story_id: int) -> dict | None:
    """One opportunity, with its line and card (the bot's buttons read it)."""
    with db.connect() as conn:
        rows = _rows(conn, "o.story_id = ?", [story_id])
    return dressed(rows)[0] if rows else None


STATUSES = {"t": "tracked", "h": "hidden", "a": "applied", "o": "open"}


def set_status(story_id: int, status: str) -> dict | None:
    """The user's choice: tracked, hidden, applied or open again. The opportunity, or None when there is none."""
    with db.transaction() as conn:
        if conn.execute("UPDATE opportunities SET status = ? WHERE story_id = ?", (status, story_id)).rowcount == 0:
            return None
    return opportunity(story_id)


def tracked(limit: int = 30) -> list[dict]:
    """Tracked opportunities by deadline (none last): /deadlines."""
    with db.connect() as conn:
        rows = _rows(conn, "o.status = 'tracked'", [])
    return dressed(sorted(rows, key=lambda o: (o["deadline_at"] is None, o["deadline_at"] or 0))[:limit])


def line(opp: dict, now: float, zone: ZoneInfo) -> str:
    """One opportunity in a list (/opps, /deadlines), HTML: «🏁 <b>Nuclear IT Hack</b> · дедлайн 1 ноя (через 7 дней) ·
    ✅ из РФ · 8/10»."""
    parts = [f"{ICON.get(opp['opp_type'], '✨')} <b>{html.escape(opp['title_ru'] or opp.get('title') or '')}</b>",
             when(opp, now, zone), ACCESS.get(opp["ru_access"] or "unclear", ACCESS["unclear"])]
    if opp["fit"] is not None:
        parts.append(f"{opp['fit']}/10")
    return " · ".join(parts)


def by_date(rows: list[dict]) -> list[dict]:
    """/opps: the tracked first, then by how soon (effective(): the deadline, else the start), unknown last, then fit."""
    zone = _zone(config.load())
    return sorted(rows, key=lambda o: (o["status"] != "tracked", effective(o, zone) is None, effective(o, zone) or 0,
                                       -(o["fit"] or 0)))


def dressed(rows: list[dict]) -> list[dict]:
    """Opportunities with their `line` and `card` written for the bot (it formats nothing itself)."""
    zone, now = _zone(config.load()), time.time()
    with db.connect() as conn:
        for row in rows:
            row.setdefault("link", _link(conn, row["story_id"]))
            row["line"], row["card"] = line(row, now, zone), card(row, now, zone)
    return rows

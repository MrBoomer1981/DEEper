"""Opportunities for the user: the app's list, updated once a morning, and reminders of tracked deadlines in Telegram.

Since 26.10.2026 new opportunities reach the user only in the app (the user's request): no digest or «hot» messages in
Telegram any more. Every tick run() does what is due:
- The morning update: every day at opps_digest_time (08:00), or once a week when opps_digest_day names a day (within
  UPDATE_LATE_HOURS, so a restart does not lose it), every open opportunity not shown yet whose date is within the
  window goes into «Радар» → «Возможности» (release(): sent_at), and those are «Новые» until the next update. One found
  during the day waits for the next morning. The very first run releases at once, so the list is not empty until then.
- The window (in_window()): the registration deadline, else the event's start (effective()), at most opps_window_days
  (92) ahead, an especially big one (`major`, read by opps.py) opps_major_window_days (183). A further one waits and
  comes in as new on the morning it enters the window; undated ones and ones open all year are always in it.
- board() is what the app shows: the released ones within the window, the new first, how many open ones lie beyond
  the window, when the list was updated and when it will be next.
- Reminders, still in Telegram: a tracked opportunity's deadline opps_reminders days ahead (14, 3, 1 by default), by
  calendar days in the settings' time zone («за 1 день» comes the day before, not the night before at 23:59), each once
  (opp_reminders); when several are due at once (tracked late), one message goes. They wait out the quiet hours. An
  approximate deadline is said to be one; rolling and unknown ones get none. Applied, hidden or expired ones get
  nothing more. Buttons «✅ Заявка подана / ✖️ Не буду» (op:<story>:a|h).
Only the types on in opps_types are shown (all are read).
"""

import html
import json
import logging
import time
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from my_browser.common.text import plural
from my_browser.news import annual, config, db, delivery

logger = logging.getLogger(__name__)

UPDATE_LATE_HOURS = 6
OVER_DAYS = 30  # «Мои»: an opportunity without the event's dates is past this long after its deadline
MONTHS_FROM_DAYS = 60  # «через 2 месяца» instead of «через 61 день»
ONE = {"hackathon": "Хакатон", "competition": "Соревнование", "school": "Школа", "internship": "Стажировка",
       "fellowship": "Программа", "education": "Поступление", "conference": "Конференция", "grant": "Грант",
       "talk": "Выступление", "exam": "Экзамен", "other": "Возможность"}
ICON = {"hackathon": "🏁", "competition": "🏆", "school": "🎓", "internship": "💼", "fellowship": "🔬", "education": "📚",
        "conference": "📄", "grant": "💰", "talk": "🎤", "exam": "📝", "other": "✨"}
ACCESS = {"yes": "✅ из РФ", "unclear": "⚠️ из РФ — неясно", "no": "⛔ не из РФ"}
PLAIN_ACCESS = {"yes": "из РФ: да", "unclear": "из РФ: неясно", "no": "из РФ: нет"}  # the app: no colored marks
ACCESS_WORDS = {"yes": "да", "unclear": "неясно", "no": "нет"}
SHORT = {"hackathon": "хакатоны", "competition": "соревнования", "school": "школы", "internship": "стажировки",
         "fellowship": "программы", "education": "поступление", "conference": "конференции", "grant": "гранты",
         "talk": "выступления", "exam": "экзамены", "other": "другое"}  # the app's filter: a phone's width
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
    mark = "≈ " if opp["deadline_kind"] == "approximate" else ""
    return f"дедлайн {mark}{moment.day} {MONTHS[moment.month - 1]} ({_left(moment, now, zone)})"


def _left(moment: datetime, now: float, zone: ZoneInfo) -> str:
    """How far a deadline is, by calendar days: «сегодня», «завтра», «через 18 дней», «через 4 месяца», «прошёл»."""
    days = (moment.date() - datetime.fromtimestamp(now, zone).date()).days
    if days > MONTHS_FROM_DAYS:
        months = round(days / 30.4)
        return f"через {months} {plural(months, 'месяц', 'месяца', 'месяцев')}"
    return "сегодня" if days == 0 else "завтра" if days == 1 else "прошёл" if days < 0 else \
        f"через {days} {plural(days, 'день', 'дня', 'дней')}"


def _dates(opp: dict) -> tuple[date | None, date | None]:
    """The event's first and last day, when the source gave them."""
    try:
        start = date.fromisoformat(opp["starts"]) if opp.get("starts") else None
        end = date.fromisoformat(opp["ends"]) if opp.get("ends") else None
    except ValueError:
        return None, None
    return start, (end if start and end and end > start else None)


def span(opp: dict) -> str | None:
    """The event's dates: «6–8 ноя», «30 ноя – 2 дек», «6 ноя»; None without a start."""
    start, end = _dates(opp)
    if not start:
        return None
    if not end:
        return f"{start.day} {MONTHS[start.month - 1]}"
    if (end.year, end.month) == (start.year, start.month):
        return f"{start.day}–{end.day} {MONTHS[start.month - 1]}"
    return f"{start.day} {MONTHS[start.month - 1]} – {end.day} {MONTHS[end.month - 1]}"


def event_when(opp: dict, now: float, zone: ZoneInfo) -> str | None:
    """The event itself, which matters once one is registered: «6–8 ноя (через 12 дней)», «(идёт)», «(прошло)»."""
    start, end = _dates(opp)
    if not start:
        return None
    today = datetime.fromtimestamp(now, zone).date()
    if today < start:
        days = (start - today).days
        left = "завтра" if days == 1 else f"через {days} {plural(days, 'день', 'дня', 'дней')}"
    elif today <= (end or start):
        left = "идёт" if end else "сегодня"
    else:
        left = "прошло"
    return f"{span(opp)} ({left})"


def over(opp: dict, now: float, zone: ZoneInfo) -> bool:
    """The event is behind: it ended (or began, when it has no end) before today; without its dates, the deadline
    passed more than OVER_DAYS ago. The app's «Мои» lists these apart, under «Прошли»."""
    start, end = _dates(opp)
    if start:
        return (end or start) < datetime.fromtimestamp(now, zone).date()
    return bool(opp["deadline_at"]) and opp["deadline_at"] < now - OVER_DAYS * 86400


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


def window_days(opp: dict, settings: config.Settings) -> int:
    """How far ahead the app shows this opportunity: further for an especially big one."""
    return max(settings.opps_window_days, settings.opps_major_window_days) if opp.get("major") else settings.opps_window_days


def in_window(opp: dict, now: float, zone: ZoneInfo, settings: config.Settings) -> bool:
    """The deadline (else the event's start) is at most window_days() ahead; undated and all-year ones always are."""
    at = effective(opp, zone)
    return at is None or at <= now + window_days(opp, settings) * 86400


def run(now: float | None = None, local: datetime | None = None) -> dict:
    """The morning update when it is due (and at the very first run), and the reminders that are due."""
    settings = config.load()
    if not settings.opps_enabled:
        return {"enabled": False}
    now = now or time.time()
    zone = _zone(settings)
    local = local or datetime.fromtimestamp(now, zone)
    key = _due_update(settings, local)
    first = db.get_state("opps_update") is None
    done: dict = {"released": None, "reminders": 0}
    with db.transaction() as conn:
        if key or first:
            done["released"] = release(conn, settings, now, zone)
        if not delivery.quiet(settings, local):
            done["reminders"] = _reminders(conn, settings, now, zone)
    if done["released"] is not None:  # after the transaction: set_state takes the same write lock
        db.set_state("opps_update", {"at": now, "new": done["released"]})
        if key:
            db.set_state("opps_updates_done", (db.get_state("opps_updates_done", []) + [key])[-10:])
        logger.info("Opportunities: the app's list updated, %d new", done["released"])
    return done


def _slot(settings: config.Settings, local: datetime) -> datetime:
    hours, minutes = map(int, settings.opps_digest_time.split(":"))
    return local.replace(hour=hours, minute=minutes, second=0, microsecond=0)


def _due_update(settings: config.Settings, local: datetime) -> str | None:
    """The update's date («2026-10-27») when it is due now and not made yet: every day, or on opps_digest_day."""
    slot = _slot(settings, local)
    key = f"{slot:%Y-%m-%d}"
    weekly = settings.opps_digest_day != config.DAILY
    if (weekly and local.weekday() != WEEKDAY[settings.opps_digest_day]) or not slot <= local < slot + timedelta(hours=UPDATE_LATE_HOURS):
        return None
    return None if key in db.get_state("opps_updates_done", []) else key


def next_update(settings: config.Settings, local: datetime) -> datetime:
    """When the list will be updated next: today's slot if it is still ahead, else the next day's (or weekday's)."""
    slot = _slot(settings, local)
    if slot <= local:
        slot += timedelta(days=1)
    if settings.opps_digest_day != config.DAILY:
        slot += timedelta(days=(WEEKDAY[settings.opps_digest_day] - slot.weekday()) % 7)
    return slot


def release(conn, settings: config.Settings, now: float, zone: ZoneInfo) -> int:
    """The morning update: every open opportunity not shown yet, of the types on, whose date is within the window,
    goes into the app's list (sent_at); how many."""
    on, types = _types(settings)
    waiting = [o for o in _rows(conn, f"o.status = 'open' AND o.sent_at IS NULL AND {on}", types)
               if in_window(o, now, zone, settings)]
    _mark_sent(conn, waiting, now)
    return len(waiting)


def _moment(at: float, now: float, zone: ZoneInfo) -> str:
    """«сегодня в 08:00», «вчера в 08:00», «завтра в 08:00», «2 ноя в 08:00»."""
    moment, today = datetime.fromtimestamp(at, zone), datetime.fromtimestamp(now, zone).date()
    days = (moment.date() - today).days
    name = {0: "сегодня", -1: "вчера", 1: "завтра"}.get(days, f"{moment.day} {MONTHS[moment.month - 1]}")
    return f"{name} в\u00a0{moment:%H:%M}"  # «в» never ends a line


def board(now: float | None = None) -> dict:
    """«Радар» → «Возможности»: the open ones released by the morning updates and within the window, the new ones
    (released by the latest update) first, by how soon and fit (order()), then the rest by how soon (by_date());
    `later`: open ones beyond the window, which come in when they enter it; `updated` and `next`: when the list was
    updated and will be, as the app writes it; `expected`: the annual events awaited and not found yet (annual.py)."""
    settings = config.load()
    zone, now = _zone(settings), now or time.time()
    at = (db.get_state("opps_update") or {}).get("at")
    on, types = _types(settings)
    with db.connect() as conn:
        rows = _rows(conn, f"o.status = 'open' AND {on}", types)
    inside = [o for o in rows if in_window(o, now, zone, settings)]
    shown = [o for o in inside if o["sent_at"]]
    for o in shown:
        o["new"] = bool(at and o["sent_at"] >= at)
    items = order([o for o in shown if o["new"]], now, zone) + by_date([o for o in shown if not o["new"]])
    return {"items": dressed(items), "new": sum(o["new"] for o in shown), "later": len(rows) - len(inside),
            "updated": _moment(at, now, zone) if at else None,
            "next": _moment(next_update(settings, datetime.fromtimestamp(now, zone)).timestamp(), now, zone),
            "window_days": settings.opps_window_days, "major_window_days": settings.opps_major_window_days,
            "expected": annual.expected(now)}


def on_board(rows: list[dict]) -> list[dict]:
    """Of listing()'s rows, what the app shows too: the user's own (tracked, applied) and the open ones the morning
    updates released, of the types on, within the window (the bot's /opps)."""
    settings = config.load()
    zone, now = _zone(settings), time.time()
    return [o for o in rows if o["status"] != "open" or (o["sent_at"] and o["opp_type"] in settings.opps_types
                                                         and in_window(o, now, zone, settings))]


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
        _enqueue(conn, "opp_reminder", text, [[["✅ Заявка подана", f"op:{opp['story_id']}:a"],
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


def meta(opp: dict, now: float, zone: ZoneInfo) -> str:
    """The app's caption, without the bot's colored marks: «Хакатон · дедлайн 12 ноя (через 18 дней) · онлайн · из РФ: да»;
    once applied, the event's dates instead of the deadline: «Хакатон · 6–8 ноя (через 12 дней) · онлайн · из РФ: да»."""
    event = event_when(opp, now, zone) if opp["status"] == "applied" else None
    parts = [ONE.get(opp["opp_type"], "Возможность"), event or when(opp, now, zone)]
    if opp["format"]:
        parts.append(FORMATS.get(opp["format"], opp["format"]))
    parts.append(PLAIN_ACCESS.get(opp["ru_access"] or "unclear", PLAIN_ACCESS["unclear"]))
    return " · ".join(parts)


def details(opp: dict, now: float, zone: ZoneInfo) -> list[list[str]]:
    """The app's sheet of one opportunity: [label, text] pairs of what is known, plain text."""
    if opp["deadline_kind"] == "rolling":
        deadline = "приём круглый год"
    elif opp["deadline_at"]:
        moment = datetime.fromtimestamp(opp["deadline_at"], zone)
        approximate = opp["deadline_kind"] == "approximate"
        deadline = (f"{'≈ ' if approximate else ''}{moment.day} {MONTHS[moment.month - 1]}"
                    f"{'' if approximate else f', {moment:%H:%M}'} ({_left(moment, now, zone)})")
    else:
        deadline = "не указан"
    facts = [["Дедлайн", deadline]]
    if opp["deadline_text"]:
        facts.append(["В анонсе", opp["deadline_text"]])
    if dates := span(opp):
        facts.append(["Даты", dates])
    if place := ", ".join(filter(None, (FORMATS.get(opp["format"] or ""), opp["location"]))):
        facts.append(["Формат", place])
    if opp["eligibility"]:
        facts.append(["Кто может", opp["eligibility"]])
    facts.append(["Из России", ACCESS_WORDS.get(opp["ru_access"] or "unclear", "неясно")])
    for label, key in (("Что дают", "reward"), ("Стоимость", "cost"), ("Организатор", "organizer")):
        if opp[key]:
            facts.append([label, opp[key]])
    return facts


def dressed(rows: list[dict]) -> list[dict]:
    """Opportunities with what the bot and the app show, written here (they format nothing themselves): `line` and
    `card` (Telegram HTML), `meta` (plain), `type_name` and `type_short`, `apply` (where to apply, else the latest
    post), and for the app's sheet and «Мои» `details` ([label, text] pairs; an especially big one says so) and
    `over` (the event is behind); `major` as a bool."""
    settings = config.load()
    zone, now = _zone(settings), time.time()
    with db.connect() as conn:
        for row in rows:
            row.setdefault("link", _link(conn, row["story_id"]))
            row["line"], row["card"], row["meta"] = line(row, now, zone), card(row, now, zone), meta(row, now, zone)
            row["type_name"] = config.OPP_TYPES.get(row["opp_type"], row["opp_type"])
            row["type_short"] = SHORT.get(row["opp_type"], row["type_name"])
            row["apply"] = row["apply_url"] or row["link"]
            row["details"], row["over"] = details(row, now, zone), over(row, now, zone)
            row["major"] = bool(row.get("major"))
            if row["major"]:
                months = round(window_days(row, settings) / 30.4)
                row["details"].append(["Масштаб", f"крупная возможность: в списке за {months} "
                                                  f"{plural(months, 'месяц', 'месяца', 'месяцев')} до срока"])
    return rows

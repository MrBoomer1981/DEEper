"""The news part's API, for the bot and core's «Радар».

In the all-in-one process they call this app in-process; on its own it runs with `uv run python -m my_browser.news`
(NEWS_PORT, default 8504) together with the news loop.
"""

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel

from my_browser.common.auth import require_token
from my_browser.news import config, db, dedup, delivery, feedback, items, loop, manage, opps, opps_delivery, scoring, sources

_seeded = False


def seeded() -> None:
    """The starting sources are in the table even when the loop does not run in this process."""
    global _seeded
    if not _seeded:
        sources.seed()
        _seeded = True


app = FastAPI(title="DEEper news", dependencies=[Depends(require_token), Depends(seeded)])


def _local(ts: float | None, tz: ZoneInfo) -> str | None:
    return datetime.fromtimestamp(ts, tz).isoformat(timespec="seconds") if ts else None


def _sources(conn, lane: str) -> dict:
    counts = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(enabled), 0) AS enabled, "
        "COALESCE(SUM(enabled AND last_ok IS NOT NULL AND last_error IS NULL), 0) AS ok, "
        "COALESCE(SUM(enabled AND polled IS NULL), 0) AS pending FROM sources WHERE lane = ?", (lane,)).fetchone()
    failing = [dict(row) for row in conn.execute(
        "SELECT name, last_error AS error, fails FROM sources WHERE lane = ? AND enabled = 1 AND last_error IS NOT NULL "
        "ORDER BY fails DESC, name", (lane,))]
    return {**dict(counts), "failing": failing}


def _deadline(conn, now: float, statuses: tuple[str, ...], zone: ZoneInfo) -> dict | None:
    """The nearest deadline ahead among opportunities of these statuses, with `when` («дедлайн 12 ноя (через 18 дней)»)."""
    row = conn.execute(
        f"SELECT story_id, title_ru, deadline, deadline_at, deadline_kind, starts, status FROM opportunities WHERE status IN "
        f"({','.join('?' * len(statuses))}) AND deadline_at > ? ORDER BY deadline_at LIMIT 1", (*statuses, now)).fetchone()
    return {**dict(row), "when": opps_delivery.when(dict(row), now, zone)} if row else None


def status() -> dict:
    """What /news in the bot shows: sources and their health, posts and stories of the last day, LLM spend (all of the
    news lane); and the opportunities lane's block: its sources, what is open, the nearest deadlines, its spend."""
    settings = config.load()
    tz = ZoneInfo(settings.timezone)
    now = time.time()
    with db.connect() as conn:
        news_sources, opps_sources = _sources(conn, "news"), _sources(conn, "opps")
        items_day = conn.execute("SELECT COUNT(*) FROM items WHERE lane = 'news' AND backfill = 0 AND seen > ?",
                                 (now - 86400,)).fetchone()[0]
        items_total = conn.execute("SELECT COUNT(*) FROM items WHERE lane = 'news'").fetchone()[0]
        stories_day = conn.execute("SELECT COUNT(*) FROM stories WHERE lane = 'news' AND first_seen > ? AND status != 'merged'",
                                   (now - 86400,)).fetchone()[0]
        stories_total = conn.execute("SELECT COUNT(*) FROM stories WHERE lane = 'news' AND status != 'merged'").fetchone()[0]
        opps_open, opps_tracked = conn.execute(
            "SELECT COALESCE(SUM(status IN ('open', 'tracked')), 0), COALESCE(SUM(status = 'tracked'), 0) "
            "FROM opportunities").fetchone()
        next_deadline, next_tracked = _deadline(conn, now, ("open", "tracked"), tz), _deadline(conn, now, ("tracked",), tz)
    spent_today, spent_month = scoring.spent(settings)
    opps_today, opps_month = scoring.spent(settings, "opps")
    board = opps_delivery.board() if settings.opps_enabled else {}
    last = db.get_state("last_collect")
    tick = db.get_state("loop_tick")
    return {
        "sources": news_sources,
        "items": {"day": items_day, "total": items_total},
        "stories": {"day": stories_day, "total": stories_total},
        "spend": {"today_rub": round(spent_today, 2), "month_rub": round(spent_month, 2),
                  "budget_month_rub": settings.budget_month_rub, "daily_budget_rub": round(settings.daily_budget_rub, 2)},
        "last_collect": {**last, "at": _local(last["at"], tz)} if last else None,
        "loop_alive": bool(tick and now - tick < 180),  # the loop marks every tick (a minute)
        # The bot delivers; the same variables as bot/telegram.token(), which this part does not import.
        "telegram": bool(os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("TELEGRAM_TOKEN")),
        "settings": settings.model_dump(),
        "opps": {
            "enabled": settings.opps_enabled, "sources": opps_sources, "open": opps_open, "tracked": opps_tracked,
            "next_deadline": next_deadline, "next_tracked": next_tracked,
            # what the app shows: in its list, of them new since the morning update, beyond the window; and when
            "shown": len(board.get("items", [])), "new": board.get("new", 0), "later": board.get("later", 0),
            "updated": board.get("updated"), "next_update": board.get("next"),
            "spend": {"today_rub": round(opps_today, 2), "month_rub": round(opps_month, 2),
                      "budget_month_rub": settings.opps_budget_month_rub,
                      "daily_budget_rub": round(settings.opps_daily_budget_rub, 2)},
        },
    }


@app.get("/status")
async def get_status() -> dict:
    return status()


@app.get("/sources")
async def get_sources() -> list[dict]:
    return sources.listing()


class SourceRequest(BaseModel):
    text: str  # a feed's address or a Telegram channel (@name, t.me/name); in the opps lane also a program's page
    lane: str = "news"  # or "opps"


@app.post("/sources")
async def post_source(request: SourceRequest) -> dict:
    """Check and add a source (or switch an existing one back on); 422 with a message for the user when refused."""
    if request.lane not in ("news", "opps"):
        raise HTTPException(422, "Полоса — news или opps.")
    try:
        return await manage.add(request.text, request.lane)
    except manage.SourceError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/sources/find")
async def find_sources(q: str) -> list[dict]:
    """Sources matching a number, an address or a part of the name."""
    return manage.find(q)


@app.delete("/sources/{source_id}")
async def delete_source(source_id: int) -> dict:
    """Switch a source off (its posts stay in their stories)."""
    if found := manage.switch(source_id, False):
        return found
    raise HTTPException(404, "source not found")


@app.get("/items")
async def get_items(limit: int = 50) -> list[dict]:
    """The latest fresh posts, newest first."""
    return items.recent(limit)


@app.get("/stories")
async def get_stories(limit: int = 30, hours: int = 48, lane: str = "news") -> list[dict]:
    """Recent stories of a lane (news or opps), the most independent sources first, each with its posts."""
    return dedup.stories(limit, hours, lane)


@app.get("/stories/{story_id}")
async def get_story(story_id: int) -> dict:
    """One story with its posts; 404 when there is none."""
    if found := delivery.story(story_id):
        return found
    raise HTTPException(404, "story not found")


@app.get("/feed")
async def get_feed(limit: int = 12, hours: int = 36) -> list[dict]:
    """«Радар»: the scored stories of the last hours, the most important first, with header and best link."""
    return delivery.feed(limit, hours)


@app.get("/deliveries")
async def get_deliveries() -> list[dict]:
    """Messages waiting for the bot: {id, kind, created, text (Telegram HTML), buttons: [[label, callback data]]}."""
    return delivery.pending()


@app.post("/deliveries/{delivery_id}/sent")
async def post_delivered(delivery_id: int) -> dict:
    return {"ok": delivery.mark_delivered(delivery_id)}


@app.post("/deliver")
async def post_deliver() -> dict:
    """Queue what is due now (urgent stories, a digest), as the loop does every minute."""
    return delivery.run()


class FeedbackRequest(BaseModel):
    story_id: int
    value: int  # +1 or -1


@app.post("/feedback")
async def post_feedback(request: FeedbackRequest) -> dict:
    feedback.record(request.story_id, request.value)
    return {"ok": True}


@app.get("/dropped")
async def get_dropped(hours: int = 24, limit: int = 30) -> list[dict]:
    """What scoring dropped in the last hours, with the reasons."""
    return delivery.dropped(hours, limit)


@app.post("/stories/{story_id}/restore")
async def post_restore(story_id: int) -> dict:
    """«Это было важно»: send a dropped story now; it counts as 👍. 404 when it is not dropped."""
    if story := delivery.restore(story_id):
        return story
    raise HTTPException(404, "no such dropped story")


@app.post("/dedup")
def post_dedup() -> dict:
    """Merge the waiting posts now (a plain def: FastAPI runs it in a thread, the embedding takes seconds)."""
    return dedup.run()


@app.post("/score")
async def post_score() -> dict:
    """Score and summarize the waiting stories now (LLM calls, within the budget)."""
    return await scoring.run()


@app.get("/opps")
async def get_opps(status: str = "", type: str = "", limit: int = 50, shown: bool = False) -> list[dict]:
    """Opportunities (open and tracked unless `status` lists others, comma-separated), the tracked first, then by
    deadline, then by fit; `type` one of config.OPP_TYPES; `shown`: of the open ones only what the app shows (released
    by a morning update, within the window, of the types on). Each with `line` and `card` (Telegram HTML) for the bot."""
    rows = opps.listing(status or None, type or None, 100_000 if shown else limit)
    if shown:
        rows = opps_delivery.on_board(rows)[:limit]
    return opps_delivery.dressed(opps_delivery.by_date(rows))


@app.get("/opps/board")
async def get_board() -> dict:
    """«Радар» → «Возможности»: `items` (as /opps gives them, plus `new`), `new`, `later`, `updated`, `next`,
    `window_days`, `major_window_days` (opps_delivery.board())."""
    return opps_delivery.board()


@app.post("/opps/run")
async def post_opps_run() -> dict:
    """Cut roundups and read the waiting opportunities now (LLM calls, within the lane's budget)."""
    return await opps.run()


@app.get("/opps/tracked")
async def get_tracked(limit: int = 30) -> list[dict]:
    """Tracked opportunities by deadline, those without one last (/deadlines)."""
    return opps_delivery.tracked(limit)


@app.get("/opps/{story_id}")
async def get_opportunity(story_id: int) -> dict:
    """One opportunity; 404 when there is none."""
    if found := opps_delivery.opportunity(story_id):
        return found
    raise HTTPException(404, "opportunity not found")


class StatusRequest(BaseModel):
    status: str  # tracked | hidden | applied | open


@app.post("/opps/{story_id}/status")
async def post_opportunity_status(story_id: int, request: StatusRequest) -> dict:
    """The user's choice (📌, ✖️, ✅, or back to open); 422 for another status, 404 when there is no such opportunity."""
    if request.status not in opps_delivery.STATUSES.values():
        raise HTTPException(422, "Статус — tracked, hidden, applied или open.")
    if found := opps_delivery.set_status(story_id, request.status):
        return found
    raise HTTPException(404, "opportunity not found")


@app.post("/collect")
async def post_collect(everything: bool = False) -> dict:
    """Poll the due sources now (every enabled one with everything=true); {"busy": true} while a poll runs."""
    return await loop.collect(everything)


@app.get("/settings")
async def get_settings() -> dict:
    return {**config.load().model_dump(), "topic_names": config.TOPICS, "opp_type_names": config.OPP_TYPES,
            "weekday_names": config.WEEKDAYS}


@app.get("/profile")
async def get_profile() -> dict:
    """The user's profile for DEEper's research follow-ups («Для меня», core/service.py): the text without contacts
    (clean_profile), and whether the user wrote it with /profile rather than living with the neutral default."""
    return {"text": config.clean_profile(config.load().opps_profile), "custom": config.is_set("opps_profile")}


@app.put("/settings")
async def put_settings(changes: dict) -> dict:
    """Changes some settings; 422 with a message for the user (in Russian) when a value is refused."""
    try:
        return config.update(changes).model_dump()
    except config.SettingsError as e:
        raise HTTPException(422, str(e)) from None


@app.get("/health")
async def health() -> dict:
    return {"ok": True}

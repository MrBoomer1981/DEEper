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
from my_browser.news import config, db, dedup, delivery, feedback, items, loop, manage, scoring, sources

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


def _sources(conn) -> dict:
    counts = conn.execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(enabled), 0) AS enabled, "
        "COALESCE(SUM(enabled AND last_ok IS NOT NULL AND last_error IS NULL), 0) AS ok, "
        "COALESCE(SUM(enabled AND polled IS NULL), 0) AS pending FROM sources WHERE lane = 'news'").fetchone()
    failing = [dict(row) for row in conn.execute(
        "SELECT name, last_error AS error, fails FROM sources WHERE lane = 'news' AND enabled = 1 AND last_error IS NOT NULL "
        "ORDER BY fails DESC, name")]
    return {**dict(counts), "failing": failing}


def status() -> dict:
    """What /news in the bot shows: sources and their health, posts and stories of the last day, LLM spend."""
    settings = config.load()
    tz = ZoneInfo(settings.timezone)
    now = time.time()
    with db.connect() as conn:
        news_sources = _sources(conn)
        items_day = conn.execute("SELECT COUNT(*) FROM items WHERE lane = 'news' AND backfill = 0 AND seen > ?",
                                 (now - 86400,)).fetchone()[0]
        items_total = conn.execute("SELECT COUNT(*) FROM items WHERE lane = 'news'").fetchone()[0]
        stories_day = conn.execute("SELECT COUNT(*) FROM stories WHERE lane = 'news' AND first_seen > ? AND status != 'merged'",
                                   (now - 86400,)).fetchone()[0]
        stories_total = conn.execute("SELECT COUNT(*) FROM stories WHERE lane = 'news' AND status != 'merged'").fetchone()[0]
    spent_today, spent_month = scoring.spent(settings)
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
    }


@app.get("/status")
async def get_status() -> dict:
    return status()


@app.get("/sources")
async def get_sources() -> list[dict]:
    return sources.listing()


class SourceRequest(BaseModel):
    text: str  # a feed's address or a Telegram channel (@name, t.me/name)
    lane: str = "news"  # the only one since the opportunities lane moved to OnePath (27.10.2026)


@app.post("/sources")
async def post_source(request: SourceRequest) -> dict:
    """Check and add a source (or switch an existing one back on); 422 with a message for the user when refused."""
    if request.lane != "news":
        raise HTTPException(422, "Возможности переехали в OnePath: источники возможностей добавляются там, в «Настройках».")
    try:
        return await manage.add(request.text)
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
async def get_stories(limit: int = 30, hours: int = 48) -> list[dict]:
    """Recent stories, the most independent sources first, each with its posts."""
    return dedup.stories(limit, hours)


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


@app.post("/collect")
async def post_collect(everything: bool = False) -> dict:
    """Poll the due sources now (every enabled one with everything=true); {"busy": true} while a poll runs."""
    return await loop.collect(everything)


@app.get("/settings")
async def get_settings() -> dict:
    return {**config.load().model_dump(), "topic_names": config.TOPICS}


@app.get("/profile")
async def get_profile() -> dict:
    """The user's profile for the research follow-ups «Для меня» (core/service.py): the text without contacts
    (clean_profile), and whether the user wrote one with /profile (it is empty until then)."""
    profile = config.load().profile
    return {"text": config.clean_profile(profile), "custom": bool(profile)}


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

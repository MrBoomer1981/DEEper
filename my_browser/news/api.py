"""The news part's API, for the bot and core's «Радар».

In the all-in-one process they call this app in-process; on its own it runs with `uv run python -m my_browser.news`
(NEWS_PORT, default 8504) together with the news loop.
"""

import os
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, HTTPException

from my_browser.common.auth import require_token
from my_browser.news import config, db, dedup, items, loop, sources

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


def status() -> dict:
    """What /news in the bot shows: sources and their health, posts and stories of the last day, LLM spend."""
    settings = config.load()
    tz = ZoneInfo(settings.timezone)
    now, today = time.time(), datetime.now(tz).strftime("%Y-%m-%d")
    with db.connect() as conn:
        counts = conn.execute(
            "SELECT COUNT(*) AS total, COALESCE(SUM(enabled), 0) AS enabled, "
            "COALESCE(SUM(enabled AND last_ok IS NOT NULL AND last_error IS NULL), 0) AS ok, "
            "COALESCE(SUM(enabled AND polled IS NULL), 0) AS pending FROM sources").fetchone()
        failing = [dict(row) for row in conn.execute(
            "SELECT name, last_error AS error, fails FROM sources WHERE enabled = 1 AND last_error IS NOT NULL "
            "ORDER BY fails DESC, name")]
        items_day = conn.execute("SELECT COUNT(*) FROM items WHERE backfill = 0 AND seen > ?", (now - 86400,)).fetchone()[0]
        items_total = conn.execute("SELECT COUNT(*) FROM items").fetchone()[0]
        stories_day = conn.execute("SELECT COUNT(*) FROM stories WHERE first_seen > ?", (now - 86400,)).fetchone()[0]
        stories_total = conn.execute("SELECT COUNT(*) FROM stories").fetchone()[0]
        spent_today = conn.execute("SELECT COALESCE(SUM(rub), 0) FROM spend WHERE day = ?", (today,)).fetchone()[0]
        spent_month = conn.execute("SELECT COALESCE(SUM(rub), 0) FROM spend WHERE day LIKE ?", (today[:7] + "%",)).fetchone()[0]
    last = db.get_state("last_collect")
    tick = db.get_state("loop_tick")
    return {
        "sources": {**dict(counts), "failing": failing},
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


@app.get("/items")
async def get_items(limit: int = 50) -> list[dict]:
    """The latest fresh posts, newest first."""
    return items.recent(limit)


@app.get("/stories")
async def get_stories(limit: int = 30, hours: int = 48) -> list[dict]:
    """Recent stories, the most independent sources first, each with its posts."""
    return dedup.stories(limit, hours)


@app.post("/dedup")
def post_dedup() -> dict:
    """Merge the waiting posts now (a plain def: FastAPI runs it in a thread, the embedding takes seconds)."""
    return dedup.run()


@app.post("/collect")
async def post_collect(everything: bool = False) -> dict:
    """Poll the due sources now (every enabled one with everything=true); {"busy": true} while a poll runs."""
    return await loop.collect(everything)


@app.get("/settings")
async def get_settings() -> dict:
    return {**config.load().model_dump(), "topic_names": config.TOPICS}


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

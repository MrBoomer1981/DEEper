"""The user's news settings: defaults here, changes in news.sqlite (the settings table), every value checked.

The bot's commands and the app change them through the API (stage 7). A wrong value is refused with a message
that says what is expected; a stored value that no longer passes falls back to its default.
"""

import json
import logging
import re
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from my_browser.news import db

logger = logging.getLogger(__name__)

TOPICS = {"models": "модели", "research": "исследования", "tools": "инструменты",
          "safety": "безопасность", "policy": "регулирование", "business": "бизнес"}
TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

# How each setting reads to the user in an error message.
LABELS = {
    "timezone": "часовой пояс", "digest_times": "время дайджеста", "digest_max_stories": "сюжетов в дайджесте",
    "urgent_enabled": "срочное", "urgent_min_importance": "порог срочности",
    "quiet_start": "начало тихих часов", "quiet_end": "конец тихих часов",
    "dedup_window_hours": "окно склейки, часов", "dedup_threshold": "порог склейки",
    "topics": "темы", "budget_month_rub": "бюджет на LLM в месяц, ₽", "paused": "пауза",
}


class SettingsError(ValueError):
    """A refused change; the message is for the user, in Russian."""


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = "Europe/Moscow"
    digest_times: list[str] = ["09:00", "18:00"]  # empty: no digests, only urgent news
    digest_max_stories: int = Field(15, ge=1, le=50)
    urgent_enabled: bool = True
    urgent_min_importance: int = Field(8, ge=1, le=10)
    quiet_start: str = "23:00"  # urgent news waits for the morning in between; equal times: no quiet hours
    quiet_end: str = "08:00"
    dedup_window_hours: int = Field(48, ge=6, le=168)
    dedup_threshold: float = Field(0.82, gt=0, lt=1)  # chosen by the eval at stage 4
    topics: list[str] = list(TOPICS)
    budget_month_rub: float = Field(100, ge=0, le=300)  # the daily limit is a thirtieth of it
    paused: bool = False

    @field_validator("timezone")
    @classmethod
    def _timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ValueError, KeyError):  # ZoneInfoNotFoundError is a KeyError
            raise ValueError(f"«{value}» не найден, нужен вида Europe/Moscow") from None
        return value

    @field_validator("digest_times")
    @classmethod
    def _times(cls, values: list[str]) -> list[str]:
        for value in values:
            _check_time(value)
        if len(set(values)) > 6:
            raise ValueError("не больше 6 дайджестов в день")
        return sorted(set(values))

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _time(cls, value: str) -> str:
        return _check_time(value)

    @field_validator("topics")
    @classmethod
    def _topics(cls, values: list[str]) -> list[str]:
        unknown = [v for v in values if v not in TOPICS]
        if unknown:
            raise ValueError(f"нет темы «{unknown[0]}», есть: {', '.join(TOPICS)}")
        return [t for t in TOPICS if t in values]

    @property
    def daily_budget_rub(self) -> float:
        return self.budget_month_rub / 30


def _check_time(value: str) -> str:
    if not TIME.match(value):
        raise ValueError(f"«{value}» — нужно время ЧЧ:ММ, например 09:00")
    return value


def explain(error: ValidationError) -> str:
    """One line per refused setting, in Russian: what is wrong and what is expected."""
    lines = []
    for problem in error.errors():
        field = str(problem["loc"][0]) if problem["loc"] else ""
        label = LABELS.get(field, field)
        kind, ctx = problem["type"], problem.get("ctx") or {}
        if kind == "extra_forbidden":
            text = f"нет такой настройки «{field}»"
            label = ""
        elif kind == "value_error":
            text = str(ctx.get("error") or problem["msg"])
        elif kind == "greater_than_equal":
            text = f"не меньше {ctx['ge']:g}"
        elif kind == "greater_than":
            text = f"больше {ctx['gt']:g}"
        elif kind == "less_than_equal":
            text = f"не больше {ctx['le']:g}"
        elif kind == "less_than":
            text = f"меньше {ctx['lt']:g}"
        elif kind.startswith("int_"):
            text = "нужно целое число"
        elif kind.startswith("float_"):
            text = "нужно число"
        elif kind.startswith("bool_"):
            text = "нужно да или нет"
        elif kind.startswith("list_"):
            text = "нужен список"
        elif kind.startswith("string_"):
            text = "нужен текст"
        else:
            text = problem["msg"]
        lines.append(f"{label}: {text}" if label else text)
    return "\n".join(lines)


def _stored() -> dict:
    with db.connect() as conn:
        return {row["key"]: json.loads(row["value"]) for row in conn.execute("SELECT key, value FROM settings")}


def load() -> Settings:
    """Current settings; stored values that no longer pass are deleted, so their defaults apply."""
    stored = _stored()
    try:
        return Settings(**stored)
    except ValidationError as e:
        bad = {str(p["loc"][0]) for p in e.errors() if p["loc"]}
        logger.error("News settings: dropping stored %s (%s)", sorted(bad), explain(e).replace("\n", "; "))
        with db.transaction() as conn:
            conn.executemany("DELETE FROM settings WHERE key = ?", [(key,) for key in bad])
        return Settings(**{k: v for k, v in stored.items() if k not in bad})


def update(changes: dict) -> Settings:
    """Apply changes, or raise SettingsError saying what is wrong; nothing is stored then."""
    try:
        merged = Settings(**{**load().model_dump(), **changes})
    except ValidationError as e:
        raise SettingsError(explain(e)) from None
    values = merged.model_dump()
    with db.transaction() as conn:
        for key in changes:
            conn.execute("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
                         (key, json.dumps(values[key], ensure_ascii=False)))
    return merged

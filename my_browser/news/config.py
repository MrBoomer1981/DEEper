"""The user's news settings: defaults here, changes in news.sqlite (the settings table), every value checked.

The bot's commands and the app change them through the API (stage 7). A wrong value is refused with a message
that says what is expected; a stored value that no longer passes falls back to its default.

The `opps_` settings are the opportunities lane's (PLAN_Opportunities.md). Its profile, against which each
opportunity's fit is scored, is the user's own text: the default here is deliberately neutral, since the repository
is public, and the user sets theirs with the bot's /profile. Contacts never reach the prompt (clean_profile()).
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
OPP_TYPES = {
    "hackathon": "хакатоны", "competition": "соревнования и олимпиады", "school": "школы и интенсивы",
    "internship": "стажировки", "fellowship": "исследовательские программы", "education": "программы с поступлением",
    "conference": "конференции и call for papers", "grant": "гранты и стипендии", "talk": "выступления",
    "exam": "экзамены и сертификаты", "other": "другое",
}
WEEKDAYS = {"mon": "понедельник", "tue": "вторник", "wed": "среда", "thu": "четверг", "fri": "пятница",
            "sat": "суббота", "sun": "воскресенье"}
DAILY = "daily"  # opps_digest_day: a digest every day (the weekly one of stage O3 left new ones waiting up to a week)
DEFAULT_PROFILE = (
    "Студент технического вуза в России, гражданин РФ. Интересы: искусственный интеллект, машинное обучение, анализ "
    "данных, программирование. Подходит: хакатоны и соревнования по ИИ и ML, школы, стажировки, исследовательские "
    "программы, конференции с подачей студенческих работ, гранты для студентов. Не подходит: акселераторы, "
    "стартап-студии, мероприятия без технической части.")
PROFILE_CHARS = (20, 3000)

# How each setting reads to the user in an error message.
LABELS = {
    "timezone": "часовой пояс", "digest_times": "время дайджеста", "digest_max_stories": "сюжетов в дайджесте",
    "urgent_enabled": "срочное", "urgent_min_importance": "порог срочности",
    "quiet_start": "начало тихих часов", "quiet_end": "конец тихих часов",
    "dedup_window_hours": "окно склейки, часов", "dedup_threshold": "порог склейки",
    "topics": "темы", "budget_month_rub": "бюджет на LLM в месяц, ₽", "paused": "пауза",
    "opps_enabled": "возможности", "opps_digest_day": "день сводки возможностей",
    "opps_digest_time": "время сводки возможностей", "opps_hot_days": "срок горящих, дней",
    "opps_hot_min_fit": "порог соответствия горящих", "opps_reminders": "напоминания о дедлайне",
    "opps_types": "типы возможностей", "opps_budget_month_rub": "бюджет возможностей на LLM в месяц, ₽",
    "opps_profile": "профиль",
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
    dedup_threshold: float = Field(0.66, gt=0, lt=1)  # eval/news, 24.10.2026: P 0.98, R 0.96 with links
    topics: list[str] = list(TOPICS)
    budget_month_rub: float = Field(100, ge=0, le=300)  # the daily limit is a fifteenth of it
    paused: bool = False  # news only: deadlines keep coming
    opps_enabled: bool = True
    opps_digest_day: str = DAILY  # the digest of new opportunities: every day, or one day a week (WEEKDAYS)
    opps_digest_time: str = "18:00"
    opps_hot_days: int = Field(7, ge=1, le=60)  # a deadline closer than this: a new opportunity goes out at once…
    opps_hot_min_fit: int = Field(6, ge=0, le=10)  # …when it fits at least this well
    opps_reminders: list[int] = [14, 3, 1]  # days before a tracked opportunity's deadline
    opps_types: list[str] = list(OPP_TYPES)  # shown; every type is collected and scored
    opps_budget_month_rub: float = Field(30, ge=0, le=300)  # apart from the news budget
    opps_profile: str = DEFAULT_PROFILE

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

    @field_validator("opps_digest_day")
    @classmethod
    def _weekday(cls, value: str) -> str:
        if value != DAILY and value not in WEEKDAYS:
            raise ValueError(f"«{value}» — нужен {DAILY} (каждый день) или день недели: {', '.join(WEEKDAYS)}")
        return value

    @field_validator("opps_digest_time")
    @classmethod
    def _opps_time(cls, value: str) -> str:
        return _check_time(value)

    @field_validator("opps_reminders")
    @classmethod
    def _reminders(cls, values: list[int]) -> list[int]:
        if any(not 1 <= v <= 60 for v in values):
            raise ValueError("за сколько дней — от 1 до 60")
        if len(set(values)) > 5:
            raise ValueError("не больше 5 напоминаний")
        return sorted(set(values), reverse=True)

    @field_validator("opps_types")
    @classmethod
    def _types(cls, values: list[str]) -> list[str]:
        unknown = [v for v in values if v not in OPP_TYPES]
        if unknown:
            raise ValueError(f"нет типа «{unknown[0]}», есть: {', '.join(OPP_TYPES)}")
        return [t for t in OPP_TYPES if t in values]

    @field_validator("opps_profile")
    @classmethod
    def _profile(cls, value: str) -> str:
        value = value.strip()
        if not PROFILE_CHARS[0] <= len(value) <= PROFILE_CHARS[1]:
            raise ValueError(f"нужно от {PROFILE_CHARS[0]} до {PROFILE_CHARS[1]} знаков, сейчас {len(value)}")
        return value

    @property
    def daily_budget_rub(self) -> float:
        """Twice an average day's share: a backlog after a restart or a busy day fits, a runaway loop does not.
        The month's budget is the real limit."""
        return self.budget_month_rub / 15

    @property
    def opps_daily_budget_rub(self) -> float:
        """A tenth of the lane's month: its first run after a deploy reads a month of announcements."""
        return self.opps_budget_month_rub / 10


CONTACTS = [
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),  # email
    re.compile(r"(?:https?://)?(?:t\.me|telegram\.me)/\S+|https?://\S+"),  # Telegram and other links
    re.compile(r"(?<!\w)@[A-Za-z][\w]{3,}"),  # a Telegram or other handle
]
PHONE = re.compile(r"\+?\d[\d\s()-]{8,}\d")


def clean_profile(text: str) -> str:
    """The profile as it goes into a prompt: without emails, links, handles and phone numbers."""
    for pattern in CONTACTS:
        text = pattern.sub("", text)
    text = PHONE.sub(lambda m: "" if sum(c.isdigit() for c in m.group(0)) >= 10 else m.group(0), text)
    text = re.sub(r"\s+([,.;:])", r"\1", " ".join(text.split()))  # what a removed contact left: «пишите , ,»
    return re.sub(r"([,;:])(?:\s*[,;:])+", r"\1", text)


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

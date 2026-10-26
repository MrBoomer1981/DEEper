"""Big recurring opportunities and when their registration usually opens: which of them the radar has not found yet.

The user's request of 26.10.2026, after the VI Congress of Young Scientists was found missing by hand: the big ones come
back every year, about the same month. EVENTS lists them with that month, as their last editions had it (checked on
26.10.2026; «обычно», never a promise). From that month on, for SEASON_MONTHS, the radar looks among its opportunities
for one that matches the event (`pattern` over the title, organizer and summary; first seen this season, or up to
FOUND_MARGIN_DAYS before it). Until one is found, expected() lists the event: the app's «Возможности» shows it under
«Ждём», and the weekly web search (discover.py) looks for it by name. An opportunity the model dropped as «not one»
does not count as found: that is a miss to see.
"""

import re
import time
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from my_browser.news import config, db

SEASON_MONTHS = 3  # how long after its usual month an event not found yet is still awaited
FOUND_MARGIN_DAYS = 45  # an announcement seen this long before the usual month counts for the season
FOUND = ("open", "tracked", "applied", "hidden", "expired")  # read as an opportunity, whatever became of it
MONTHS_IN = ("январе", "феврале", "марте", "апреле", "мае", "июне", "июле", "августе", "сентябре", "октябре", "ноябре",
             "декабре")


@dataclass(frozen=True)
class Annual:
    key: str
    name: str
    opp_type: str  # config.OPP_TYPES: an event of a type switched off is not awaited
    months: tuple[int, ...]  # when its registration usually opens
    url: str  # the official page, or "" when it could not be checked
    pattern: str  # case-insensitive, over the opportunity's title, organizer and summary

    def matches(self, text: str) -> bool:
        return re.search(self.pattern, text, re.IGNORECASE) is not None


EVENTS = (
    Annual("congress", "Конгресс молодых учёных", "conference", (9,), "https://конгресс.наука.рф/",
           r"конгресс\w*\s+молод\w*\s+уч[её]н(?![^.]{0,40}итмо)|congress of young scientists"),
    Annual("kmu-itmo", "Конгресс молодых учёных ИТМО", "conference", (12,), "https://kmu.itmo.ru/",
           r"кму\s+итмо|конгресс\w*\s+молод\w*\s+уч[её]н\w*\s+(?:университета\s+)?итмо"),
    Annual("yandex-cup", "Yandex Cup", "competition", (6,), "https://yandex.ru/cup", r"yandex\s*cup"),
    Annual("aij-contest", "AIJ Contest (AI Journey)", "competition", (8,), "https://aij.ru/contest",
           r"aij\s*contest|ai\s*journey\s*contest"),
    Annual("ya-profi", "Олимпиада «Я — профессионал»", "competition", (9,), "https://yandex.ru/profi/",
           r"я\s*[—–-]\s*профессионал"),
    Annual("icpc", "ICPC: отборы студенческих команд", "competition", (9,), "https://nerc.itmo.ru/", r"\bicpc\b|\bnerc\b"),
    Annual("potanin", "Стипендиальная программа Фонда Потанина", "grant", (10,), "https://fondpotanin.ru/", r"потанин"),
    Annual("rosmolodez-grants", "Росмолодёжь.Гранты: конкурс молодёжных проектов", "grant", (2, 8), "https://myrosmol.ru/",
           r"росмолод\w*\.?\s*гранты|конкурс\w*\s+молод[её]жн\w*\s+проект"),
    Annual("shad", "Поступление в ШАД", "education", (4,), "https://shad.yandex.ru/",
           r"\bшад\b|школ\w*\s+анализа\s+данных|school of data analysis"),
    Annual("dls", "Deep Learning School", "education", (1, 8), "https://dls.samcs.ru/", r"deep\s+learning\s+school|\bdls\b"),
    Annual("yandex-intern", "Летние стажировки Яндекса", "internship", (2,), "https://yandex.ru/yaintern",
           r"стажир\w*[^.]{0,40}яндекс|яндекс[^.]{0,40}стажир|yandex[^.]{0,30}intern"),
    Annual("safeboard", "Kaspersky SafeBoard", "internship", (2, 8), "https://safeboard.kaspersky.ru/", r"safeboard"),
    Annual("eeml", "EEML: летняя школа", "school", (2,), "https://www.eeml.eu/", r"\beeml\b"),
    Annual("m2l", "M2L: летняя школа", "school", (2,), "https://www.m2lschool.org/",
           r"\bm2l\b|mediterranean machine learning"),
    Annual("smiles", "SMILES: летняя школа Сколтеха", "school", (4,), "https://smiles.skoltech.ru/", r"\bsmiles\b"),
    Annual("airi-school", "Летняя школа AIRI", "school", (4,), "https://airi.net/ru/summer-school",
           r"летн\w*\s+школ\w*[^.]{0,30}airi|airi[^.]{0,30}летн\w*\s+школ|airi summer school"),
    Annual("mats", "MATS", "fellowship", (1, 8), "https://www.matsprogram.org/", r"\bmats\b"),
    Annual("leaders", "Лидеры цифровой трансформации", "hackathon", (4,), "",
           r"лидер\w*\s+цифров\w*\s+трансформац"),
    Annual("dialogue", "Диалог: конференция по компьютерной лингвистике", "conference", (1,), "https://dialogue-conf.org/",
           r"dialogue\s+(?:conference|20\d\d)|конференци\w*\s+[«\"]?диалог"),
)


def season(event: Annual, today: date) -> date | None:
    """The first day of the event's season that includes today (its usual month, this year or the last), or None."""
    for year in (today.year, today.year - 1):
        for month in event.months:
            start = date(year, month, 1)
            end_month = month + SEASON_MONTHS
            end = date(year + (end_month - 1) // 12, (end_month - 1) % 12 + 1, 1)
            if start <= today < end:
                return start
    return None


def expected(now: float | None = None) -> list[dict]:
    """The events whose season is on and which the radar has not found yet, the most recently opened first: `key`,
    `name`, `opp_type`, `url`, `when` («обычно открывается в сентябре»)."""
    settings = config.load()
    zone = ZoneInfo(settings.timezone)
    now = now or time.time()
    today = datetime.fromtimestamp(now, zone).date()
    with db.connect() as conn:
        found = [(row["first_seen"] or 0, " ".join(filter(None, (row["title_ru"], row["title"], row["organizer"], row["summary_ru"]))))
                 for row in conn.execute(
                     f"SELECT o.title_ru, o.organizer, o.summary_ru, s.title, s.first_seen FROM opportunities o "
                     f"JOIN stories s ON s.id = o.story_id WHERE o.status IN ({','.join('?' * len(FOUND))})", FOUND)]
    waiting = []
    for event in EVENTS:
        start = season(event, today)
        if start is None or event.opp_type not in settings.opps_types:
            continue
        since = datetime(start.year, start.month, 1, tzinfo=zone).timestamp() - FOUND_MARGIN_DAYS * 86400
        if not any(seen >= since and event.matches(text) for seen, text in found):
            waiting.append({"key": event.key, "name": event.name, "opp_type": event.opp_type, "url": event.url,
                            "when": f"обычно открывается в {MONTHS_IN[start.month - 1]}", "start": start.isoformat()})
    return sorted(waiting, key=lambda e: e["start"], reverse=True)

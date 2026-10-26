"""How far a research is, from what the worker already reports: its phase, its planned queries and the tail
of its log. The percent moves only with real counters (search batches done, sections written, claims
checked), never with the clock; where a phase has no counter it stays put. The worker's phase texts and
their patterns are in common/phases.py; «В очереди», «Прервано» and the like are core's own (core/jobs.py).
"""

import re
import time

from my_browser.common import phases
from my_browser.common.report import OVERVIEW_MIN
from my_browser.common.text import plural

SCRAPED = re.compile(r"Scraped (\d+) pages")  # gpt-researcher, once per search batch
DEEP_WORDS = 3000  # about what a deep report runs to: its draft's share of the writing


def _reading(job: dict) -> tuple[float, int]:
    """(share of the planned search batches done, pages read) from the job's log tail."""
    log = job.get("log") or []
    batches = [int(m.group(1)) for line in log if (m := SCRAPED.search(line))]
    return min(1.0, len(batches) / max(1, len(job.get("queries") or []))), sum(batches)


def percent(job: dict) -> int | None:
    """0-100 while running, None when it cannot be told (queued, stopped)."""
    if job["status"] == "done":
        return 100
    if job["status"] != "running":
        return None
    phase, mode = job.get("phase") or "", job.get("mode")
    late = {phases.FILES: 97, phases.FOLLOWUPS: 93, phases.FIXING: 91, phases.LOOKING: 90}
    for start, value in late.items():
        if phase.startswith(start):
            return value
    if m := phases.CHECKING.search(phase):
        return 82 + round(7 * int(m.group(1)) / max(1, int(m.group(2))))
    if phase == phases.PREFLIGHT:
        return 2
    if mode == "detailed":
        if phase == phases.BROAD_SEARCH:
            return 4 + round(14 * _reading(job)[0])
        if m := phases.RESEARCHED.search(phase):
            return 20 + round(36 * int(m.group(1)) / max(1, int(m.group(2))))
        if m := phases.SECTION.search(phase):
            return 56 + round(22 * (int(m.group(1)) - 1) / max(1, int(m.group(2))))
        if phase == phases.CONCLUSION:
            return 78
    if m := phases.DEPTH.search(phase):
        return 4 + round(58 * int(m.group(3)) / max(1, int(m.group(4))))
    if phase == phases.READING:
        return 4 + round(58 * _reading(job)[0])
    if phase == phases.WRITING:  # the draft (worker/draft.py) tells how far the text is
        written = len((job.get("draft") or "").split())
        return 66 + round(14 * min(1.0, written / (OVERVIEW_MIN if mode == "quick" else DEEP_WORDS)))
    return None


def status(job: dict) -> tuple[str, str]:
    """(what is going on, a detail): «Читаю источники», «прочитано 21 из 39»."""
    phase = job.get("phase") or ""
    if job["status"] == "queued":
        return "В очереди", ""
    if phase == phases.PREFLIGHT:
        return "Готовлюсь", "проверяю модель и поиск"
    if phase in (phases.READING, phases.BROAD_SEARCH):
        _, read = _reading(job)
        urls = len(job.get("urls") or [])
        return "Читаю источники", f"прочитано {read} из {urls}" if urls else ""
    if m := phases.DEPTH.search(phase):
        return f"Копаю вглубь · уровень {m.group(1)} из {m.group(2)}", f"запросов {m.group(3)} из {m.group(4)}"
    if m := phases.RESEARCHED.search(phase):
        return "Исследую разделы", f"готово {m.group(1)} из {m.group(2)}"
    if m := phases.SECTION.search(phase):
        return f"Пишу раздел {m.group(1)} из {m.group(2)}", m.group(3).strip()
    if m := phases.CHECKING.search(phase):
        return "Проверяю факты", f"{m.group(1)} из {m.group(2)}"
    if phase == phases.WRITING and (written := len((job.get("draft") or "").split())):
        return "Пишу отчёт", f"написано {written} {plural(written, 'слово', 'слова', 'слов')}"
    for start, text in ((phases.LOOKING, "Ищу подтверждение"), (phases.FIXING, "Исправляю расхождения"),
                        (phases.FOLLOWUPS, "Подбираю, что копать дальше"), (phases.FILES, "Собираю PDF и DOCX"),
                        (phases.CONCLUSION, "Пишу заключение"), (phases.WRITING, "Пишу отчёт")):
        if phase.startswith(start):
            return text, ""
    return phase.rstrip("."), ""


def remaining(job: dict, value: int | None) -> str:
    """«ещё ~8 мин», extrapolated from the time so far; "" until the percent means something."""
    if not value or value < 8 or value >= 100 or job.get("elapsed", 0) < 20:
        return ""
    minutes = job["elapsed"] * (100 - value) / value / 60
    return "меньше минуты" if minutes < 1 else f"ещё ~{round(minutes)} мин"


def held(job: dict) -> int:
    """Seconds a queued job is still held back before the worker may take it (one tap on a follow-up), else 0."""
    return max(0, round((job.get("not_before") or 0) - time.time()))


def position(job: dict, active: list[dict]) -> int:
    """1 for the next research to run; researches are taken oldest first."""
    return 1 + sum(1 for other in active if other["id"] != job["id"] and other["created"] < job["created"])

"""How far a research is, from what the worker already reports: its phase, its planned queries and the tail
of its log. The percent moves only with real counters (search batches done, sections written, claims
checked), never with the clock; where a phase has no counter it stays put. Phase texts are the worker's
(worker/pipeline.py, detailed.py, factcheck.py, runner.py) and core's queue.
"""

import re

SECTION = re.compile(r"^Раздел (\d+)/(\d+)")
DEPTH = re.compile(r"^Глубина (\d+)/(\d+), запросов (\d+)/(\d+)")
CHECKING = re.compile(r"^Проверяю факты: (\d+) из (\d+)")
SCRAPED = re.compile(r"Scraped (\d+) pages")  # gpt-researcher, once per search batch


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
    late = {"Собираю PDF": 97, "Подбираю, что исследовать": 93, "Исправляю": 91, "Ищу подтверждение": 90}
    for start, value in late.items():
        if phase.startswith(start):
            return value
    if phase.startswith("Проверяю факты"):
        m = CHECKING.search(phase)
        return 82 + (round(7 * int(m.group(1)) / int(m.group(2))) if m else 0)
    if phase.startswith("Проверяю DeepSeek"):
        return 2
    if mode == "detailed":
        if phase.startswith("Общий поиск"):
            return 4 + round(14 * _reading(job)[0])
        if phase.startswith("Разделов:"):
            return 20
        if m := SECTION.search(phase):
            return 20 + round(56 * (int(m.group(1)) - 1) / int(m.group(2)))
        if phase.startswith("Пишу заключение"):
            return 78
    if m := DEPTH.search(phase):
        return 4 + round(58 * int(m.group(3)) / max(1, int(m.group(4))))
    if phase.startswith("Ищу и читаю"):
        return 4 + round(58 * _reading(job)[0])
    if phase.startswith("Пишу отчёт"):
        return 66
    return None


def status(job: dict) -> tuple[str, str]:
    """(what is going on, a detail): «Читаю источники», «прочитано 21 из 39»."""
    phase = job.get("phase") or ""
    if job["status"] == "queued":
        return "В очереди", ""
    if phase.startswith("Проверяю DeepSeek"):
        return "Готовлюсь", "проверяю модель и поиск"
    if phase.startswith(("Ищу и читаю", "Общий поиск")):
        _, read = _reading(job)
        urls = len(job.get("urls") or [])
        return "Читаю источники", f"прочитано {read} из {urls}" if urls else ""
    if m := DEPTH.search(phase):
        return f"Копаю вглубь · уровень {m.group(1)} из {m.group(2)}", f"запросов {m.group(3)} из {m.group(4)}"
    if phase.startswith("Разделов:"):
        return "Пишу введение", phase.split(".", 1)[0].lower()
    if m := SECTION.search(phase):
        return f"Пишу раздел {m.group(1)} из {m.group(2)}", phase.split(":", 1)[-1].strip()
    if m := CHECKING.search(phase):
        return "Проверяю факты", f"{m.group(1)} из {m.group(2)}"
    for start, text in (("Проверяю факты", "Проверяю факты"), ("Ищу подтверждение", "Ищу подтверждение"),
                        ("Исправляю", "Исправляю расхождения"), ("Подбираю", "Подбираю, что копать дальше"),
                        ("Собираю PDF", "Собираю PDF и DOCX"), ("Пишу заключение", "Пишу заключение"),
                        ("Пишу отчёт", "Пишу отчёт")):
        if phase.startswith(start):
            return text, ""
    return phase.rstrip("."), ""


def remaining(job: dict, value: int | None) -> str:
    """«ещё ~8 мин», extrapolated from the time so far; "" until the percent means something."""
    if not value or value < 8 or value >= 100 or job.get("elapsed", 0) < 20:
        return ""
    minutes = job["elapsed"] * (100 - value) / value / 60
    return "меньше минуты" if minutes < 1 else f"ещё ~{round(minutes)} мин"


def position(job: dict, active: list[dict]) -> int:
    """1 for the next research to run; researches are taken oldest first."""
    return 1 + sum(1 for other in active if other["id"] != job["id"] and other["created"] < job["created"])

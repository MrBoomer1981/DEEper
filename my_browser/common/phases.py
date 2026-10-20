"""A running job's phase: the line in which the worker says what it is doing, and how core reads it back.

The worker reports the line with its progress; the bot shows it as it is, and core's job page turns it
into a percent and a status line (core/ui/progress.py). Both sides take the wording from here, so
changing it changes both. A phase with a counter has its pattern right under the function that writes
it; a line core cannot read only loses its percent.
"""

import re

from my_browser.common.text import plural

PREFLIGHT = "Проверяю DeepSeek и поиск..."  # worker/runner.py, before the research
READING = "Ищу и читаю источники..."  # quick and deep (worker/pipeline.py)
WRITING = "Пишу отчёт..."  # quick and deep
BROAD_SEARCH = "Общий поиск по теме..."  # detailed (worker/detailed.py): the pass before the subtopics
CONCLUSION = "Пишу заключение..."  # detailed
FOLLOWUPS = "Подбираю, что исследовать дальше..."  # worker/runner.py, after the research and its check
FILES = "Собираю PDF и DOCX..."  # worker/runner.py, last


def depth(level: int, levels: int, done: int, total: int) -> str:
    """Deep mode: gpt-researcher's own counters."""
    return f"Глубина {level}/{levels}, запросов {done}/{total}"


DEPTH = re.compile(r"^Глубина (\d+)/(\d+), запросов (\d+)/(\d+)")


def researched(done: int, total: int) -> str:
    """Detailed mode: subtopics researched (all at once)."""
    return f"Исследую разделы: готово {done} из {total}"


RESEARCHED = re.compile(r"^Исследую разделы: готово (\d+) из (\d+)")


def section(number: int, total: int, title: str) -> str:
    """Detailed mode: the section being written (one after another)."""
    return f"Раздел {number}/{total}: {title}"


SECTION = re.compile(r"^Раздел (\d+)/(\d+): ?(.*)")


def checking(done: int, total: int) -> str:
    """Fact-check (worker/factcheck.py): claims judged so far."""
    return f"Проверяю факты: {done} из {total}"


CHECKING = re.compile(r"^Проверяю факты: (\d+) из (\d+)")
LOOKING = "Ищу подтверждение в других источниках"


def looking(count: int) -> str:
    """Fact-check: claims whose page did not back them, looked for in the other pages read."""
    return f"{LOOKING}: {count}..."


FIXING = "Исправляю"


def fixing(count: int) -> str:
    """Fact-check: sentences the writer model corrects."""
    return f"{FIXING} {count} {plural(count, 'предложение', 'предложения', 'предложений')} по источникам..."

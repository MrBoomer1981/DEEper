"""What several parts read in a report: the sections the worker appends to its files (core strips them from the
page and shows them its own way), the 5–7 minute report's own sections, and how long a report reads.

«Проверка фактов» (worker/factcheck.py) comes first among the appended sections, «Что исследовать дальше» last.
"""

import re

from my_browser.common.text import MARKDOWN_LINK

# Kinds of follow-up topics: the prompt asks for FOLLOWUP_KINDS (worker/suggest.py); «Проверить» is only in reports
# saved before 26.10.2026. The order is the order they are shown in.
KINDS = {"deeper": "Глубже", "against": "Против", "practice": "На практике", "personal": "Для меня",
         "fresh": "Свежее", "adjacent": "Рядом", "verify": "Проверить"}
FOLLOWUP_KINDS = ("deeper", "against", "practice", "personal", "fresh", "adjacent")
# The 5–7 minute report's own sections (worker/prompts.py asks for them), by the report's language: its summary
# first, its open questions last.
OVERVIEW_HEADINGS = {"russian": ("Главное", "Чего пока не знаем"), "english": ("Key points", "What we don't know yet")}
# Its length in words of text, references not counted, asked of the writer: about OVERVIEW_WORDS, at least OVERVIEW_MIN,
# never over OVERVIEW_MAX. Pro writes a fifth to a third less than asked here, the less the thinner the sources: for «about
# 1100» it wrote 804–1000 words on four eval topics (26.10.2026), for «about 1200, at least 1000» 830 on a basic question;
# 5–7 minutes at common.text.READING_WPM is 900–1260 words, so the ask is above it.
OVERVIEW_WORDS, OVERVIEW_MIN, OVERVIEW_MAX = 1300, 1100, 1450
FACTCHECK_TITLE = "## Проверка фактов"
FOLLOWUPS_TITLE = "## Что исследовать дальше"
ANY_ANSWER = "не важно"  # the clarification answer that adds nothing to the topic
# How a fact-check verdict reads to the user (worker/factcheck.py writes them in lower case).
VERDICT_TEXT = {"partial": "подтверждено частично", "mismatch": "в источнике другие данные",
                "contradicted": "источник говорит обратное", "not_found": "в источнике этого нет"}


def body(markdown: str) -> str:
    """The report without the sections appended to its files."""
    for title in (FOLLOWUPS_TITLE, FACTCHECK_TITLE):
        head, found, _ = markdown.rpartition(f"\n{title}\n")
        if found:
            markdown = head.rstrip()
    return markdown


def appended(markdown: str, title: str) -> str:
    """The text of a section appended to the report, without its heading; "" when there is none."""
    _, found, tail = markdown.rpartition(f"\n{title}\n")
    if not found:
        return ""
    return tail.split(f"\n{FOLLOWUPS_TITLE}\n")[0].strip() if title == FACTCHECK_TITLE else tail.strip()


def section(topics: list[dict]) -> str:
    if not topics:
        return ""
    items = "\n".join(f"- **{KINDS[t['kind']]}.** {t['query']}" + (f" — {t['why']}" if t["why"] else "") for t in topics)
    return f"\n\n{FOLLOWUPS_TITLE}\n\n{items}\n"



SUMMARY_HEADING = re.compile(r"^##\s+(?:Главное|Key points)\s*$", re.M)
CONCLUSION_HEADING = re.compile(r"^##\s+(?:Заключение|Выводы|Итоги|Conclusions?)\b[^\n]*$", re.M | re.I)
REFERENCES_HEADING = re.compile(r"^#{1,2}\s+(?:Источники|Список (?:источников|литературы)|Литература|Ссылки|References|Sources|"
                                r"Bibliography)\b[^\n]*$", re.M | re.I)
H2 = re.compile(r"^##\s", re.M)


def _section(text: str, heading: re.Pattern) -> str | None:
    """The text under the first heading that matches, up to the next ## heading."""
    match = heading.search(text)
    if not match:
        return None
    end = H2.search(text, match.end())
    return text[match.end():end.start() if end else len(text)].strip()


def summary(markdown: str, words: int = 120) -> str:
    """What a report found, in brief and without links: its «Главное», else its conclusion, else its opening.
    Lines are kept (the summary is a list of points), and the whole is cut to about `words` words."""
    text = MARKDOWN_LINK.sub(r"\1", body(markdown))
    found = _section(text, SUMMARY_HEADING) or _section(text, CONCLUSION_HEADING)
    if found is None:
        found = "\n".join(line for line in text.splitlines() if not line.startswith("#"))
    lines, count = [], 0
    for line in (" ".join(line.split()) for line in found.splitlines()):
        if not line:
            continue
        lines.append(" ".join(line.split(" ")[:max(0, words - count)]))
        count += len(line.split(" "))
        if count >= words:
            break
    return "\n".join(line for line in lines if line)


def reading_words(markdown: str) -> int:
    """The words a reader reads: the report without its appended sections, its list of sources and its citations."""
    text = body(markdown)
    references = [m for m in REFERENCES_HEADING.finditer(text) if m.start() > len(text) // 3]
    if references:
        text = text[:references[-1].start()]
    return len(MARKDOWN_LINK.sub("", text).split())

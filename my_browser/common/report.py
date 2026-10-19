"""Sections the worker appends to a report's files, which core strips from the page and shows its own way.

«Проверка фактов» (worker/factcheck.py) comes first, «Что исследовать дальше» last.
"""

KINDS = {"deeper": "Углубить", "adjacent": "Рядом", "verify": "Проверить"}
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

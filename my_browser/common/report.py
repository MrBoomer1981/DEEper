"""The «Что исследовать дальше» section: written by the worker, stripped and shown as cards by core."""

KINDS = {"deeper": "Углубить", "adjacent": "Рядом", "verify": "Проверить"}
SECTION_TITLE = "## Что исследовать дальше"
ANY_ANSWER = "не важно"  # the clarification answer that adds nothing to the topic


def body(markdown: str) -> str:
    """The report without the follow-ups section appended to its files."""
    head, found, _ = markdown.rpartition(f"\n{SECTION_TITLE}\n")
    return head.rstrip() if found else markdown


def section(topics: list[dict]) -> str:
    if not topics:
        return ""
    items = "\n".join(f"- **{KINDS[t['kind']]}.** {t['query']}" + (f" — {t['why']}" if t["why"] else "") for t in topics)
    return f"\n\n{SECTION_TITLE}\n\n{items}\n"

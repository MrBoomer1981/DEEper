"""Short versions of reports (at most 1 500 words), kept next to them as <name>.short.md.

The worker writes one once per report (POST /shorten); the web app shows it at /report/{id}?short=1.
"""

import re
from pathlib import Path

# The worker ends a short version with the list of the sources it cites, under one of these headings.
SOURCES = re.compile(r"^## (?:Источники|References)\s*$", re.M)


def short_path(md_path: Path) -> Path:
    return md_path.with_suffix(".short.md")


def load_short(md_path: Path) -> str | None:
    path = short_path(md_path)
    return path.read_text("utf-8") if path.exists() else None


def save_short(md_path: Path, markdown: str) -> None:
    short_path(md_path).write_text(markdown, "utf-8")


def words(markdown: str) -> int:
    """The words a reader reads: the text without its list of sources."""
    found = list(SOURCES.finditer(markdown))
    return len((markdown[:found[-1].start()] if found else markdown).split())

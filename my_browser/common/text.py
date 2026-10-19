"""Small text helpers used by several parts: URLs, file names, money, Russian plurals."""

import re
from urllib.parse import quote, urlparse

_ENCODED_RUN = re.compile(r"(?:%[0-9A-Fa-f]{2})+")
# [anchor](url) with a web URL; the URL may hold balanced parentheses, as in «…/Облачные_сервисы_(рынок_России)»
MARKDOWN_LINK = re.compile(r"\[([^\]]*)\]\((https?://(?:[^()\s]|\([^()\s]*\))+)\)")


def host(url: str) -> str:
    return (urlparse(url).hostname or "").lower().removeprefix("www.")


def readable_url(url: str) -> str:
    """Decode percent-encoded non-ASCII text (Cyrillic slugs) but keep ASCII escapes like %2F or %20.

    Encoded Cyrillic costs the writer model 3-4 tokens per letter; long reports with many such
    links hit the output limit and got cut off mid-reference. Requests re-encodes these URLs.
    """
    def decode(match: re.Match) -> str:
        raw = bytes.fromhex(match.group(0).replace("%", ""))
        if all(b < 0x80 for b in raw):
            return match.group(0)
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            return match.group(0)
        return "".join(quote(ch, safe="") if ord(ch) < 0x80 else ch for ch in text)

    return _ENCODED_RUN.sub(decode, url)


def slugify(text: str, max_len: int = 60) -> str:
    slug = re.sub(r"[^\w]+", "-", text.lower()).strip("-")
    return slug[:max_len].rstrip("-") or "report"


def rub_text(rub: float | None) -> str:
    if rub is None:
        return "—"
    return f"{rub:.2f} ₽".replace(".", ",") if rub < 10 else f"{rub:.1f} ₽".replace(".", ",")


def plural(n: int, one: str, few: str, many: str) -> str:
    """The Russian word form for n: 1 отчёт, 2 отчёта, 5 отчётов."""
    if n % 10 == 1 and n % 100 != 11:
        return one
    return few if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14 else many

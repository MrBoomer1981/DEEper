"""
Text processing utilities: tokenization, chunking, cleaning.
"""
import re
from typing import List

import tiktoken

_ENCODER = None


def _get_encoder() -> tiktoken.Encoding:
    global _ENCODER
    if _ENCODER is None:
        _ENCODER = tiktoken.get_encoding("cl100k_base")
    return _ENCODER


def count_tokens(text: str) -> int:
    """Return approximate token count for a text string."""
    return len(_get_encoder().encode(text))


def chunk_text(
    text: str,
    chunk_size: int = 800,
    overlap: int = 150,
) -> List[str]:
    """
    Split text into overlapping token-based chunks.

    Args:
        text: Input text.
        chunk_size: Max tokens per chunk.
        overlap: Overlapping tokens between consecutive chunks.

    Returns:
        List of text chunks.
    """
    encoder = _get_encoder()
    tokens = encoder.encode(text)

    if len(tokens) <= chunk_size:
        return [text]

    chunks: List[str] = []
    start = 0
    while start < len(tokens):
        end = min(start + chunk_size, len(tokens))
        chunk_tokens = tokens[start:end]
        chunks.append(encoder.decode(chunk_tokens))
        if end == len(tokens):
            break
        start += chunk_size - overlap

    return chunks


def clean_html_text(text: str) -> str:
    """Remove excess whitespace and non-printable characters from scraped text."""
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text)
    # Remove non-printable characters (keep newlines)
    text = re.sub(r"[^\x09\x0A\x0D\x20-\x7E\u00A0-\uFFFF]", "", text)
    return text.strip()


def truncate_text(text: str, max_tokens: int = 4000) -> str:
    """Truncate text to max_tokens, preserving whole words."""
    encoder = _get_encoder()
    tokens = encoder.encode(text)
    if len(tokens) <= max_tokens:
        return text
    return encoder.decode(tokens[:max_tokens])


def extract_urls_from_text(text: str) -> List[str]:
    """Extract all HTTP/HTTPS URLs from a block of text."""
    pattern = r"https?://[^\s\]>\"')]*"
    return re.findall(pattern, text)


def format_sources(sources: List[str]) -> str:
    """Format a list of source URLs as a numbered markdown list."""
    if not sources:
        return "_No sources available._"
    lines = [f"{i + 1}. {url}" for i, url in enumerate(sources)]
    return "\n".join(lines)


def sanitize_filename(name: str) -> str:
    """Convert arbitrary text to a safe filename."""
    name = re.sub(r"[^\w\s-]", "", name)
    name = re.sub(r"\s+", "_", name.strip())
    return name[:100]

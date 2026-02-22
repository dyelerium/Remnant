"""Markdown-aware text chunker — preserves headings as metadata."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TextChunk:
    text: str
    heading: Optional[str] = None          # Nearest Markdown heading
    heading_level: int = 0                 # H1=1, H2=2 …
    char_offset: int = 0                   # Byte offset in source document
    metadata: dict = field(default_factory=dict)


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)


def chunk_markdown(
    text: str,
    max_tokens: int = 300,
    overlap_tokens: int = 50,
    words_per_token: float = 0.75,
) -> list[TextChunk]:
    """
    Split Markdown text into overlapping chunks.
    Respects heading boundaries where possible.
    Returns list of TextChunk with heading metadata attached.
    """
    # Rough word-to-token ratio (1 token ≈ 0.75 words for English prose)
    max_words = int(max_tokens / words_per_token)
    overlap_words = int(overlap_tokens / words_per_token)

    # Parse heading positions
    headings: list[tuple[int, int, str]] = []  # (pos, level, text)
    for m in _HEADING_RE.finditer(text):
        headings.append((m.start(), len(m.group(1)), m.group(2).strip()))

    def _current_heading(pos: int) -> tuple[int, str]:
        """Return (level, heading_text) for the nearest heading before pos."""
        h_level, h_text = 0, ""
        for h_pos, level, h_t in headings:
            if h_pos <= pos:
                h_level, h_text = level, h_t
            else:
                break
        return h_level, h_text

    words = text.split()
    # Map word index → character offset (approx)
    char_offsets: list[int] = []
    pos = 0
    for w in words:
        char_offsets.append(text.find(w, pos))
        pos = char_offsets[-1] + len(w)

    chunks: list[TextChunk] = []
    start = 0

    while start < len(words):
        end = min(start + max_words, len(words))
        chunk_words = words[start:end]
        chunk_text = " ".join(chunk_words)
        char_pos = char_offsets[start] if start < len(char_offsets) else 0

        h_level, h_text = _current_heading(char_pos)

        chunks.append(
            TextChunk(
                text=chunk_text,
                heading=h_text or None,
                heading_level=h_level,
                char_offset=char_pos,
            )
        )

        if end >= len(words):
            break
        start = end - overlap_words
        if start <= chunks[-2].char_offset // max(1, len(" ".join(words).split(maxsplit=1)[0])) if len(chunks) >= 2 else False:
            start = end  # Avoid infinite loop on degenerate input

    return chunks


def chunk_plain(
    text: str,
    max_tokens: int = 300,
    overlap_tokens: int = 50,
) -> list[TextChunk]:
    """Simple word-based chunker for non-Markdown text."""
    max_words = int(max_tokens * 0.75)
    overlap_words = int(overlap_tokens * 0.75)
    words = text.split()
    chunks: list[TextChunk] = []
    start = 0

    while start < len(words):
        end = min(start + max_words, len(words))
        chunks.append(TextChunk(text=" ".join(words[start:end])))
        if end >= len(words):
            break
        start = end - overlap_words

    return chunks


def auto_chunk(
    text: str,
    max_tokens: int = 300,
    overlap_tokens: int = 50,
) -> list[TextChunk]:
    """Auto-detect Markdown and dispatch to appropriate chunker."""
    if _HEADING_RE.search(text):
        return chunk_markdown(text, max_tokens, overlap_tokens)
    return chunk_plain(text, max_tokens, overlap_tokens)

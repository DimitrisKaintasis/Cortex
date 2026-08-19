from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class TextChunk:
    position: int
    char_start: int
    char_end: int
    text: str


class TextChunker:
    """Deterministic character chunking with preferred natural boundaries."""

    def __init__(self, max_chars: int = 1_200, overlap_chars: int = 200) -> None:
        if max_chars < 100:
            raise ValueError("max_chars must be at least 100")
        if overlap_chars < 0:
            raise ValueError("overlap_chars cannot be negative")
        if overlap_chars >= max_chars:
            raise ValueError("overlap_chars must be smaller than max_chars")
        self.max_chars = max_chars
        self.overlap_chars = overlap_chars

    def split(self, text: str) -> tuple[TextChunk, ...]:
        if not text or not text.strip():
            raise ValueError("text cannot be empty")

        chunks: list[TextChunk] = []
        start = 0
        position = 0
        text_length = len(text)

        while start < text_length:
            end = min(text_length, start + self.max_chars)
            if end < text_length:
                end = self._preferred_boundary(text, start, end)

            leading = len(text[start:end]) - len(text[start:end].lstrip())
            trailing = len(text[start:end].rstrip())
            content_start = start + leading
            content_end = start + trailing

            if content_start < content_end:
                chunks.append(
                    TextChunk(
                        position=position,
                        char_start=content_start,
                        char_end=content_end,
                        text=text[content_start:content_end],
                    )
                )
                position += 1

            if end >= text_length:
                break

            next_start = end - self.overlap_chars
            if next_start <= start:
                next_start = end
            start = next_start

        return tuple(chunks)

    def _preferred_boundary(self, text: str, start: int, proposed_end: int) -> int:
        minimum = start + (self.max_chars // 2)
        candidates = (
            text.rfind("\n\n", minimum, proposed_end),
            text.rfind(". ", minimum, proposed_end),
            text.rfind("\n", minimum, proposed_end),
            text.rfind(" ", minimum, proposed_end),
        )
        boundary = max(candidates)
        if boundary < minimum:
            return proposed_end
        return boundary + 1

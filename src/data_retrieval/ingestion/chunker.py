from __future__ import annotations

from dataclasses import dataclass
from io import StringIO
from typing import TextIO


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

        return tuple(self.iter_stream(StringIO(text)))

    def iter_stream(self, stream: TextIO, *, read_size: int = 65_536):
        """Yield deterministic chunks while keeping only a bounded text window in memory."""

        if read_size < self.max_chars:
            raise ValueError("read_size must be at least max_chars")

        buffer = ""
        buffer_start = 0
        position = 0
        exhausted = False

        while True:
            while len(buffer) <= self.max_chars and not exhausted:
                block = stream.read(read_size)
                if block:
                    buffer += block
                else:
                    exhausted = True
            if not buffer and exhausted:
                break

            end = min(len(buffer), self.max_chars)
            if not exhausted or end < len(buffer):
                end = self._preferred_boundary(buffer, 0, end)

            segment = buffer[:end]
            leading = len(segment) - len(segment.lstrip())
            trailing = len(segment.rstrip())
            content_start = buffer_start + leading
            content_end = buffer_start + trailing

            if content_start < content_end:
                yield TextChunk(
                    position=position,
                    char_start=content_start,
                    char_end=content_end,
                    text=segment[leading:trailing],
                )
                position += 1

            if exhausted and end >= len(buffer):
                break

            next_start = end - self.overlap_chars
            if next_start <= 0:
                next_start = end
            buffer = buffer[next_start:]
            buffer_start += next_start

    def _preferred_boundary(self, text: str, start: int, proposed_end: int) -> int:
        minimum = start + (self.max_chars // 2)
        for separator in ("\n\n", ". ", "\n", " "):
            boundary = text.rfind(separator, minimum, proposed_end)
            if boundary >= minimum:
                return boundary + 1
        return proposed_end

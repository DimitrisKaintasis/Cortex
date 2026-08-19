from __future__ import annotations

import math
from typing import Protocol


class Embedder(Protocol):
    @property
    def provider(self) -> str: ...

    @property
    def model(self) -> str: ...

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]: ...


def cosine_similarity(left: tuple[float, ...], right: tuple[float, ...]) -> float:
    if len(left) != len(right) or not left:
        return 0.0
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0.0:
        return 0.0
    return sum(a * b for a, b in zip(left, right, strict=True)) / denominator

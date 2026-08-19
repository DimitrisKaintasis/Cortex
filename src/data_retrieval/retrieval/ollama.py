from __future__ import annotations

import math
from dataclasses import dataclass

from data_retrieval.tagging.ollama import OllamaError, OllamaJsonClient


@dataclass(frozen=True, slots=True)
class OllamaEmbedder:
    base_url: str
    model_name: str
    timeout_seconds: float = 120.0
    truncate: bool = True

    def __post_init__(self) -> None:
        OllamaJsonClient(self.base_url, self.model_name, self.timeout_seconds)

    @property
    def provider(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        return self.model_name

    def embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        if not texts:
            return ()
        response = OllamaJsonClient(self.base_url, self.model_name, self.timeout_seconds).post_json(
            "/api/embed",
            {
                "model": self.model_name,
                "input": list(texts),
                "truncate": self.truncate,
            },
        )
        raw_embeddings = response.get("embeddings")
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != len(texts):
            raise OllamaError("Ollama returned an invalid embedding batch")
        embeddings: list[tuple[float, ...]] = []
        dimensions: int | None = None
        for raw_vector in raw_embeddings:
            if not isinstance(raw_vector, list) or not raw_vector:
                raise OllamaError("Ollama returned an invalid embedding vector")
            if any(
                isinstance(value, bool)
                or not isinstance(value, int | float)
                or not math.isfinite(float(value))
                for value in raw_vector
            ):
                raise OllamaError("Ollama embedding vector contains invalid values")
            vector = tuple(float(value) for value in raw_vector)
            dimensions = dimensions or len(vector)
            if len(vector) != dimensions:
                raise OllamaError("Ollama embedding dimensions changed within one batch")
            embeddings.append(vector)
        return tuple(embeddings)

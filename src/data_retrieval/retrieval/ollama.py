from __future__ import annotations

import math
from dataclasses import dataclass

from data_retrieval.tagging.ollama import OllamaError, OllamaJsonClient


@dataclass(frozen=True, slots=True)
class EmbeddingProfile:
    name: str
    query_prefix: str = ""
    document_prefix: str = ""


EMBEDDING_PROFILES = {
    "symmetric": EmbeddingProfile(name="symmetric"),
    "harrier-retrieval-v1": EmbeddingProfile(
        name="harrier-retrieval-v1",
        query_prefix=(
            "Instruct: Given a search query, retrieve relevant stored passages "
            "that answer or provide evidence for the query\nQuery: "
        ),
    ),
}


@dataclass(frozen=True, slots=True)
class OllamaEmbedder:
    base_url: str
    model_name: str
    timeout_seconds: float = 120.0
    truncate: bool = True
    profile_name: str = "symmetric"

    def __post_init__(self) -> None:
        OllamaJsonClient(self.base_url, self.model_name, self.timeout_seconds)
        if self.profile_name not in EMBEDDING_PROFILES:
            raise ValueError(f"unknown embedding profile: {self.profile_name}")

    @property
    def provider(self) -> str:
        return "ollama"

    @property
    def model(self) -> str:
        if self.profile_name == "symmetric":
            return self.model_name
        return f"{self.model_name}::{self.profile_name}"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        profile = EMBEDDING_PROFILES[self.profile_name]
        return self._embed(tuple(f"{profile.document_prefix}{text}" for text in texts))

    def embed_query(self, text: str) -> tuple[float, ...]:
        profile = EMBEDDING_PROFILES[self.profile_name]
        vectors = self._embed((f"{profile.query_prefix}{text}",))
        if len(vectors) != 1:
            raise OllamaError("Ollama returned an invalid query embedding")
        return vectors[0]

    def _embed(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
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

from __future__ import annotations

from dataclasses import dataclass

from data_retrieval.domain.models import Tag
from data_retrieval.retrieval.embedding import Embedder, cosine_similarity
from data_retrieval.tagging.normalization import normalize_tag


@dataclass(frozen=True, slots=True)
class CanonicalTagMatch:
    candidate: str
    tag: Tag
    similarity: float


class SemanticTagCanonicalizer:
    """Catalog-first semantic matching; unmatched terms remain proposed-new tags."""

    def __init__(self, embedder: Embedder, *, threshold: float = 0.90) -> None:
        if not 0.0 <= threshold <= 1.0:
            raise ValueError("threshold must be between 0 and 1")
        self.embedder = embedder
        self.threshold = threshold
        self._catalog_cache: dict[
            tuple[tuple[str, str], ...], tuple[tuple[float, ...], ...]
        ] = {}

    @property
    def evidence_source(self) -> str:
        return f"semantic-catalog:{self.embedder.provider}:{self.embedder.model}"

    def resolve(
        self, *, candidates: tuple[str, ...], catalog: tuple[Tag, ...]
    ) -> dict[str, CanonicalTagMatch]:
        normalized = tuple(
            dict.fromkeys(value for candidate in candidates if (value := normalize_tag(candidate)))
        )
        if not normalized or not catalog:
            return {}
        catalog_key = tuple((tag.tag_id, tag.canonical_text) for tag in catalog)
        catalog_vectors = self._catalog_cache.get(catalog_key)
        if catalog_vectors is None:
            catalog_vectors = self.embedder.embed_documents(
                tuple(tag.canonical_text for tag in catalog)
            )
            if len(catalog_vectors) != len(catalog):
                raise ValueError("embedder returned the wrong number of catalog vectors")
            self._catalog_cache[catalog_key] = catalog_vectors
        candidate_vectors = self.embedder.embed_documents(normalized)
        if len(candidate_vectors) != len(normalized):
            raise ValueError("embedder returned the wrong number of candidate vectors")

        matches: dict[str, CanonicalTagMatch] = {}
        for candidate, vector in zip(normalized, candidate_vectors, strict=True):
            best_tag: Tag | None = None
            best_similarity = -1.0
            for tag, catalog_vector in zip(catalog, catalog_vectors, strict=True):
                similarity = cosine_similarity(vector, catalog_vector)
                if similarity > best_similarity:
                    best_tag = tag
                    best_similarity = similarity
            if best_tag is not None and best_similarity >= self.threshold:
                matches[candidate] = CanonicalTagMatch(candidate, best_tag, best_similarity)
        return matches

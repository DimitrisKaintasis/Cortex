from __future__ import annotations

import math
from dataclasses import dataclass

from data_retrieval.domain.models import TagCandidateState
from data_retrieval.services.tag_lifecycle import TagLifecycleService
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class BenchmarkTagResolutionResult:
    namespace: str
    proposed_count: int
    promoted_count: int
    merged_count: int
    rejected_count: int
    minimum_confidence: float
    profile: str = "benchmark-exact-tag-resolution-v1"


class BenchmarkTagCatalogResolver:
    """Resolve exact proposal families only inside an explicit public benchmark run."""

    def __init__(self, repository: Repository, *, minimum_confidence: float = 0.65) -> None:
        if not math.isfinite(minimum_confidence) or not 0.0 <= minimum_confidence <= 1.0:
            raise ValueError("minimum_confidence must be finite and in [0, 1]")
        self.repository = repository
        self.minimum_confidence = minimum_confidence

    def resolve_namespace(self, namespace: str) -> BenchmarkTagResolutionResult:
        candidates = self.repository.list_tag_candidates(
            namespace=namespace,
            state=TagCandidateState.PROPOSED,
        )
        grouped = {}
        for candidate in candidates:
            grouped.setdefault(candidate.normalized_text, []).append(candidate)
        lifecycle = TagLifecycleService(self.repository)
        promoted = 0
        merged = 0
        rejected = 0
        for normalized_text, family in sorted(grouped.items()):
            ordered = sorted(
                family,
                key=lambda candidate: (-candidate.confidence, candidate.candidate_id),
            )
            existing = self.repository.get_tags_by_canonical(
                namespace=namespace,
                canonical_texts=(normalized_text,),
            )
            if existing:
                for candidate in ordered:
                    lifecycle.merge(candidate.candidate_id, normalized_text)
                    merged += 1
                continue
            if ordered[0].confidence < self.minimum_confidence:
                for candidate in ordered:
                    lifecycle.reject(
                        candidate.candidate_id,
                        reason=(
                            "benchmark auto-resolution rejected family below "
                            f"confidence {self.minimum_confidence:.2f}"
                        ),
                    )
                    rejected += 1
                continue
            lifecycle.promote(ordered[0].candidate_id)
            promoted += 1
            for candidate in ordered[1:]:
                lifecycle.merge(candidate.candidate_id, normalized_text)
                merged += 1
        return BenchmarkTagResolutionResult(
            namespace=namespace,
            proposed_count=len(candidates),
            promoted_count=promoted,
            merged_count=merged,
            rejected_count=rejected,
            minimum_confidence=self.minimum_confidence,
        )

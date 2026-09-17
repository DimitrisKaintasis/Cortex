"""Opt-in, bounded semantic priors, separate from behavioral co-occurrence."""

import math
from dataclasses import dataclass

from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import CalibrationSignal, CalibrationTarget, TagRelation
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer


@dataclass(frozen=True)
class TagSimilarityPolicy:
    version: str = "tag-similarity-prior-v1"
    lower_threshold: float = 0.70
    maximum_neighbors: int = 3
    maximum_prior: float = 0.25
    catalog_limit: int = 500

    def __post_init__(self):
        if not self.version.strip():
            raise ValueError("version is required")
        if not math.isfinite(self.lower_threshold) or not 0 <= self.lower_threshold < 1:
            raise ValueError("lower threshold must be in [0,1)")
        if not math.isfinite(self.maximum_prior) or not 0 < self.maximum_prior <= 1:
            raise ValueError("maximum prior must be in (0,1]")
        if self.maximum_neighbors < 1 or self.catalog_limit < 2:
            raise ValueError("positive neighbor count and catalog limit >=2 required")


class TagSimilarityCalibrationService:
    def __init__(
        self,
        repository: Repository,
        canonicalizer: SemanticTagCanonicalizer,
        policy: TagSimilarityPolicy | None = None,
    ):
        self.repository = repository
        self.canonicalizer = canonicalizer
        self.policy = policy or TagSimilarityPolicy()
        if self.policy.lower_threshold >= canonicalizer.threshold:
            raise ValueError("relationship threshold must be below merge threshold")

    def calibrate(self, namespace: str) -> dict[str, int]:
        p = self.policy
        catalog = tuple(self.repository.list_tags(namespace, limit=p.catalog_limit + 1))
        if len(catalog) > p.catalog_limit:
            raise ValueError("catalog exceeds bounded similarity calibration limit")
        if len(catalog) < 2:
            return {"created": 0, "eligible": 0}
        candidates = sorted(
            (
                (min(a.tag_id, b.tag_id), max(a.tag_id, b.tag_id), s)
                for a, b, s in self.canonicalizer.catalog_similarities(catalog)
                if math.isfinite(s) and p.lower_threshold <= s < self.canonicalizer.threshold
            ),
            key=lambda x: (-x[2], x[0], x[1]),
        )
        existing = self.repository.get_tag_relations_touching(
            tag_ids=tuple(t.tag_id for t in catalog), relation_type="semantic_similarity"
        )
        keys = {(e.source_tag_id, e.target_tag_id) for e in existing}
        degree = {t.tag_id: 0 for t in catalog}
        for e in existing:
            for tid in (e.source_tag_id, e.target_tag_id):
                degree[tid] = degree.get(tid, 0) + 1
        signals, edges = [], []
        for left, right, similarity in candidates:
            if (left, right) in keys or max(degree[left], degree[right]) >= p.maximum_neighbors:
                continue
            # Pair identity is stable across reruns/models. Refresh requires explicit policy,
            # never silently stack another provider's prior onto an existing serving pair.
            signal_id = stable_id("tag-similarity-prior", namespace, left, right)
            if signal_id in self.repository.get_calibration_signal_ids((signal_id,)):
                continue
            signal = CalibrationSignal(
                signal_id=signal_id,
                namespace=namespace,
                target_type=CalibrationTarget.TAG_RELATION,
                target_id=left,
                related_id=right,
                relation_type="semantic_similarity",
                signal_type="semantic_similarity_prior",
                value=similarity,
                confidence=1.0,
                multiplier=1.0,
                provider=self.canonicalizer.evidence_source,
                profile_version=p.version,
                metadata={
                    "cosine_similarity": similarity,
                    "lower_threshold": p.lower_threshold,
                    "upper_threshold": self.canonicalizer.threshold,
                    "maximum_prior": p.maximum_prior,
                    "maximum_neighbors": p.maximum_neighbors,
                    "interpretation": "relatedness prior, not probability or observed usefulness",
                },
            )
            signals.append(signal)
            edges.append(
                TagRelation(
                    left,
                    right,
                    "semantic_similarity",
                    p.maximum_prior * similarity,
                    1.0,
                    (signal_id,),
                )
            )
            degree[left] += 1
            degree[right] += 1
        if signals:
            self.repository.apply_calibration_updates(
                signals=tuple(signals), atom_tags=(), atom_links=(), tag_relations=tuple(edges)
            )
        return {"created": len(edges), "eligible": len(candidates)}

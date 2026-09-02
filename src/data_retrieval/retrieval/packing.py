from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass

from data_retrieval.domain.models import AtomRole
from data_retrieval.retrieval.models import RetrievalItem

TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", re.UNICODE)


@dataclass(frozen=True, slots=True)
class EvidencePackingPolicy:
    minimum_source_fraction: float = 0.40
    maximum_derived_fraction: float = 0.50
    near_duplicate_threshold: float = 0.90

    def __post_init__(self) -> None:
        for name, value in (
            ("minimum_source_fraction", self.minimum_source_fraction),
            ("maximum_derived_fraction", self.maximum_derived_fraction),
            ("near_duplicate_threshold", self.near_duplicate_threshold),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class EvidencePackingResult:
    items: tuple[RetrievalItem, ...]
    diagnostics: dict[str, object]


class EvidencePacker:
    """Apply trust, provenance, and diversity constraints after relevance ranking."""

    def __init__(self, policy: EvidencePackingPolicy | None = None) -> None:
        self.policy = policy or EvidencePackingPolicy()

    def pack(
        self, ranked: list[RetrievalItem], *, top_k: int
    ) -> EvidencePackingResult:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        source_candidates = [item for item in ranked if item.atom_role is AtomRole.SOURCE]
        source_target = min(
            len(source_candidates),
            max(1, math.ceil(top_k * self.policy.minimum_source_fraction)),
        ) if source_candidates else 0
        derived_limit = (
            max(0, math.floor(top_k * self.policy.maximum_derived_fraction))
            if source_candidates
            else top_k
        )

        selected: list[RetrievalItem] = source_candidates[:source_target]
        selected_ids = {item.atom_id for item in selected}
        excluded = Counter[str]()
        derived_count = 0

        for item in ranked:
            if len(selected) >= top_k:
                break
            if item.atom_id in selected_ids:
                continue
            if item.atom_role is AtomRole.DERIVED:
                if derived_count >= derived_limit:
                    excluded["derived_cap"] += 1
                    continue
                reason = self._duplicate_reason(item, selected)
                if reason is not None:
                    excluded[reason] += 1
                    continue
                derived_count += 1
            selected.append(item)
            selected_ids.add(item.atom_id)

        rank = {item.atom_id: index for index, item in enumerate(ranked)}
        selected.sort(key=lambda item: rank[item.atom_id])
        role_counts = Counter(item.atom_role.value for item in selected)
        diagnostics: dict[str, object] = {
            "policy": {
                "minimum_source_fraction": self.policy.minimum_source_fraction,
                "maximum_derived_fraction": self.policy.maximum_derived_fraction,
                "near_duplicate_threshold": self.policy.near_duplicate_threshold,
            },
            "source_candidates": len(source_candidates),
            "source_target": source_target,
            "source_selected": role_counts[AtomRole.SOURCE.value],
            "derived_limit": derived_limit,
            "selected_by_role": dict(sorted(role_counts.items())),
            "excluded": dict(sorted(excluded.items())),
            "underfilled": len(selected) < min(top_k, len(ranked)),
        }
        return EvidencePackingResult(tuple(selected), diagnostics)

    def _duplicate_reason(
        self, candidate: RetrievalItem, selected: list[RetrievalItem]
    ) -> str | None:
        candidate_lineage = frozenset(candidate.lineage_atom_ids)
        for existing in selected:
            if existing.atom_role is not AtomRole.DERIVED:
                continue
            existing_lineage = frozenset(existing.lineage_atom_ids)
            if candidate_lineage and candidate_lineage == existing_lineage:
                return "lineage_equivalent"
            if self._similarity(candidate.content, existing.content) >= (
                self.policy.near_duplicate_threshold
            ):
                return "near_duplicate"
        return None

    @staticmethod
    def _similarity(left: str, right: str) -> float:
        left_terms = set(TOKEN_PATTERN.findall(left.casefold()))
        right_terms = set(TOKEN_PATTERN.findall(right.casefold()))
        if not left_terms or not right_terms:
            return 1.0 if left.strip().casefold() == right.strip().casefold() else 0.0
        return len(left_terms & right_terms) / len(left_terms | right_terms)

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, replace
from enum import StrEnum
from typing import Any

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomLink,
    AtomLinkRelation,
    CalibrationSignal,
    CalibrationTarget,
    utc_now,
)
from data_retrieval.retrieval.embedding import Embedder, cosine_similarity
from data_retrieval.storage.repository import Repository


class Mem0AdmissionDisposition(StrEnum):
    PROVISIONAL = "provisional"
    HOLD_FOR_REVIEW = "hold_for_review"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class Mem0VectorAdmissionPolicy:
    """Versioned cold-start policy; vectors corroborate but never invent edges."""

    profile: str = "mem0-vector-cold-start-v1"
    reject_below_similarity: float = 0.60
    provisional_above_similarity: float = 0.80
    provisional_weight_cap: float = 0.25
    require_both_endpoints_in_evidence: bool = True

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("Mem0 vector admission profile cannot be empty")
        for name in (
            "reject_below_similarity",
            "provisional_above_similarity",
            "provisional_weight_cap",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if self.provisional_above_similarity <= self.reject_below_similarity:
            raise ValueError("provisional similarity must exceed rejection similarity")

    @property
    def profile_id(self) -> str:
        fingerprint = content_hash(
            json.dumps(asdict(self), sort_keys=True, separators=(",", ":"))
        )[:12]
        return f"{self.profile}:{fingerprint}"

    def classify(
        self,
        *,
        similarity: float,
        lexical_endpoint_support: bool,
        confidence: float,
    ) -> tuple[Mem0AdmissionDisposition, float, tuple[str, ...]]:
        for name, value in (("similarity", similarity), ("confidence", confidence)):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be between 0 and 1")
        if not lexical_endpoint_support and self.require_both_endpoints_in_evidence:
            return (
                Mem0AdmissionDisposition.HOLD_FOR_REVIEW,
                0.0,
                ("endpoint_not_explicit_in_evidence",),
            )
        if similarity < self.reject_below_similarity:
            return (
                Mem0AdmissionDisposition.REJECTED,
                0.0,
                ("low_vector_corroboration",),
            )
        if similarity < self.provisional_above_similarity:
            return (
                Mem0AdmissionDisposition.HOLD_FOR_REVIEW,
                0.0,
                ("ambiguous_vector_corroboration",),
            )
        scaled = (similarity - self.reject_below_similarity) / (
            1.0 - self.reject_below_similarity
        )
        weight = min(
            self.provisional_weight_cap,
            self.provisional_weight_cap * scaled * confidence,
        )
        return (
            Mem0AdmissionDisposition.PROVISIONAL,
            weight,
            ("vector_and_lexical_corroboration",),
        )


@dataclass(frozen=True, slots=True)
class Mem0AdmissionDecision:
    from_atom_id: str
    to_atom_id: str
    predicates: tuple[str, ...]
    support_atom_ids: tuple[str, ...]
    vector_similarity: float
    lexical_endpoint_support: bool
    disposition: Mem0AdmissionDisposition
    initial_weight: float
    reason_codes: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["disposition"] = self.disposition.value
        return payload


@dataclass(frozen=True, slots=True)
class Mem0VectorCalibrationResult:
    namespace: str
    links_examined: int
    links_calibrated: int
    provisional_links: int
    held_links: int
    rejected_links: int
    replayed_links: int
    scope_truncated: bool
    decisions: tuple[Mem0AdmissionDecision, ...]
    decisions_truncated: bool
    policy_profile: str
    embedding_provider: str
    embedding_model: str

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "decisions": [decision.as_dict() for decision in self.decisions],
        }


class Mem0VectorCalibrationService:
    """Assign bounded cold-start weights to provenance-valid Mem0 proposals."""

    def __init__(
        self,
        repository: Repository,
        embedder: Embedder,
        *,
        policy: Mem0VectorAdmissionPolicy | None = None,
        decision_report_limit: int = 1_000,
    ) -> None:
        if decision_report_limit < 0:
            raise ValueError("decision_report_limit cannot be negative")
        self.repository = repository
        self.embedder = embedder
        self.policy = policy or Mem0VectorAdmissionPolicy()
        self.decision_report_limit = decision_report_limit

    def calibrate_namespace(
        self, namespace: str, *, max_links: int | None = None
    ) -> Mem0VectorCalibrationResult:
        if not namespace.strip():
            raise ValueError("namespace cannot be empty")
        if max_links is not None and max_links <= 0:
            raise ValueError("max_links must be positive")
        all_links = tuple(
            link
            for link in self.repository.list_atom_links(
                namespace=namespace,
                relation=AtomLinkRelation.MEM0_ENTITY_RELATION,
            )
            if link.metadata.get("admission_state") != "reviewed"
            and "proposal_weight_raw" in link.metadata
        )
        signal_ids = tuple(self._signal_id(namespace, link) for link in all_links)
        existing = self.repository.get_calibration_signal_ids(signal_ids)
        all_pending = tuple(
            (signal_id, link)
            for signal_id, link in zip(signal_ids, all_links, strict=True)
            if signal_id not in existing
        )
        pending = all_pending[:max_links] if max_links is not None else all_pending
        scope_truncated = len(pending) < len(all_pending)
        if not pending:
            return self._result(
                namespace=namespace,
                links_examined=len(all_links),
                links_calibrated=0,
                replayed_links=len(existing),
                decisions=(),
                scope_truncated=False,
            )

        atoms = self._load_atoms(tuple(link for _, link in pending))
        representations = tuple(
            self._relationship_text(link, atoms) for _, link in pending
        )
        evidence_texts = tuple(self._evidence_text(link, atoms) for _, link in pending)
        vectors = self.embedder.embed_documents((*representations, *evidence_texts))
        if len(vectors) != len(pending) * 2:
            raise ValueError("embedder returned the wrong number of calibration vectors")
        split = len(pending)
        relationship_vectors = vectors[:split]
        evidence_vectors = vectors[split:]

        decisions: list[Mem0AdmissionDecision] = []
        signals: list[CalibrationSignal] = []
        updated_links: list[AtomLink] = []
        for (signal_id, link), relationship_vector, evidence_vector in zip(
            pending,
            relationship_vectors,
            evidence_vectors,
            strict=True,
        ):
            similarity = min(
                1.0,
                max(0.0, cosine_similarity(relationship_vector, evidence_vector)),
            )
            lexical_support = self._has_lexical_endpoint_support(link, atoms)
            decision = self._decide(link, similarity, lexical_support)
            decisions.append(decision)
            signals.append(self._signal(namespace, signal_id, link, decision))
            updated_links.append(
                replace(
                    link,
                    weight_raw=decision.initial_weight,
                    updated_at=utc_now(),
                    metadata={
                        **link.metadata,
                        "admission_state": decision.disposition.value,
                        "admission_policy": self.policy.profile_id,
                        "vector_similarity": decision.vector_similarity,
                        "vector_provider": self.embedder.provider,
                        "vector_model": self.embedder.model,
                        "lexical_endpoint_support": decision.lexical_endpoint_support,
                        "admission_reason_codes": list(decision.reason_codes),
                    },
                )
            )
        self.repository.apply_calibration_updates(
            signals=tuple(signals),
            atom_tags=(),
            atom_links=tuple(updated_links),
            tag_relations=(),
        )
        return self._result(
            namespace=namespace,
            links_examined=len(all_links),
            links_calibrated=len(pending),
            replayed_links=len(existing),
            decisions=tuple(decisions),
            scope_truncated=scope_truncated,
        )

    def _decide(
        self, link: AtomLink, similarity: float, lexical_support: bool
    ) -> Mem0AdmissionDecision:
        disposition, initial_weight, reasons = self.policy.classify(
            similarity=similarity,
            lexical_endpoint_support=lexical_support,
            confidence=link.confidence,
        )
        return Mem0AdmissionDecision(
            from_atom_id=link.from_atom_id,
            to_atom_id=link.to_atom_id,
            predicates=self._predicates(link),
            support_atom_ids=self._support_ids(link),
            vector_similarity=round(similarity, 6),
            lexical_endpoint_support=lexical_support,
            disposition=disposition,
            initial_weight=round(initial_weight, 6),
            reason_codes=reasons,
        )

    def _signal(
        self,
        namespace: str,
        signal_id: str,
        link: AtomLink,
        decision: Mem0AdmissionDecision,
    ) -> CalibrationSignal:
        return CalibrationSignal(
            signal_id=signal_id,
            namespace=namespace,
            target_type=CalibrationTarget.ATOM_LINK,
            target_id=link.from_atom_id,
            related_id=link.to_atom_id,
            relation_type=AtomLinkRelation.MEM0_ENTITY_RELATION.value,
            signal_type="mem0_vector_cold_start",
            value=decision.vector_similarity,
            confidence=link.confidence,
            multiplier=1.0,
            provider=self.embedder.provider,
            profile_version=self.policy.profile_id,
            source_reference=f"{link.from_atom_id}->{link.to_atom_id}",
            metadata={
                "embedding_model": self.embedder.model,
                "disposition": decision.disposition.value,
                "initial_weight": decision.initial_weight,
                "reason_codes": list(decision.reason_codes),
                "support_atom_ids": list(decision.support_atom_ids),
                "predicates": list(decision.predicates),
            },
        )

    def _signal_id(self, namespace: str, link: AtomLink) -> str:
        return stable_id(
            "calibration",
            namespace,
            self.policy.profile_id,
            self.embedder.provider,
            self.embedder.model,
            link.from_atom_id,
            link.to_atom_id,
            *self._predicates(link),
            *self._support_ids(link),
        )

    def _load_atoms(self, links: tuple[AtomLink, ...]) -> dict[str, Atom]:
        atom_ids = tuple(
            dict.fromkeys(
                atom_id
                for link in links
                for atom_id in (
                    link.from_atom_id,
                    link.to_atom_id,
                    *self._support_ids(link),
                )
            )
        )
        return {atom.atom_id: atom for atom in self.repository.get_atoms(atom_ids)}

    def _relationship_text(self, link: AtomLink, atoms: dict[str, Atom]) -> str:
        source = atoms.get(link.from_atom_id)
        target = atoms.get(link.to_atom_id)
        predicates = " and ".join(value.replace("_", " ") for value in self._predicates(link))
        return " ".join(
            value
            for value in (
                source.content if source else "",
                predicates,
                target.content if target else "",
            )
            if value
        )

    def _evidence_text(self, link: AtomLink, atoms: dict[str, Atom]) -> str:
        return "\n\n".join(
            atoms[atom_id].content
            for atom_id in self._support_ids(link)
            if atom_id in atoms
        )

    def _has_lexical_endpoint_support(
        self, link: AtomLink, atoms: dict[str, Atom]
    ) -> bool:
        source = atoms.get(link.from_atom_id)
        target = atoms.get(link.to_atom_id)
        if source is None or target is None:
            return False
        evidence = _normalized_text(self._evidence_text(link, atoms))
        return _contains_normalized(evidence, source.content) and _contains_normalized(
            evidence, target.content
        )

    @staticmethod
    def _predicates(link: AtomLink) -> tuple[str, ...]:
        values = link.metadata.get("predicates", ())
        return tuple(sorted({str(value) for value in values if str(value).strip()}))

    @staticmethod
    def _support_ids(link: AtomLink) -> tuple[str, ...]:
        values = link.metadata.get("support_atom_ids", ())
        return tuple(dict.fromkeys(str(value) for value in values if str(value).strip()))

    def _result(
        self,
        *,
        namespace: str,
        links_examined: int,
        links_calibrated: int,
        replayed_links: int,
        decisions: tuple[Mem0AdmissionDecision, ...],
        scope_truncated: bool,
    ) -> Mem0VectorCalibrationResult:
        reported = decisions[: self.decision_report_limit]
        return Mem0VectorCalibrationResult(
            namespace=namespace,
            links_examined=links_examined,
            links_calibrated=links_calibrated,
            provisional_links=sum(
                item.disposition is Mem0AdmissionDisposition.PROVISIONAL
                for item in decisions
            ),
            held_links=sum(
                item.disposition is Mem0AdmissionDisposition.HOLD_FOR_REVIEW
                for item in decisions
            ),
            rejected_links=sum(
                item.disposition is Mem0AdmissionDisposition.REJECTED
                for item in decisions
            ),
            replayed_links=replayed_links,
            scope_truncated=scope_truncated,
            decisions=reported,
            decisions_truncated=len(reported) < len(decisions),
            policy_profile=self.policy.profile_id,
            embedding_provider=self.embedder.provider,
            embedding_model=self.embedder.model,
        )


def _normalized_text(value: str) -> str:
    return " ".join(part for part in re.split(r"[^\w]+", value.casefold()) if part)


def _contains_normalized(normalized_text: str, value: str) -> bool:
    needle = _normalized_text(value)
    return bool(needle) and f" {needle} " in f" {normalized_text} "

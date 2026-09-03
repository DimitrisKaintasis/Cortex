from __future__ import annotations

import math
from dataclasses import dataclass, replace
from itertools import combinations

from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomRole,
    AtomTag,
    CalibrationSignal,
    CalibrationTarget,
    TagRelation,
    utc_now,
)
from data_retrieval.retrieval.embedding import Embedder, cosine_similarity
from data_retrieval.retrieval.models import AtomEmbedding
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class Mem0RelationshipCalibrationProfile:
    """Bounded cold-start weights for Mem0 proposals and vector corroboration."""

    version: str = "mem0-joint-bootstrap-v1"
    mem0_atom_tag_step: float = 0.10
    mem0_relation_step: float = 0.10
    vector_atom_tag_step: float = 0.05
    vector_relation_step: float = 0.05
    unsupported_memory_confidence: float = 0.75

    def __post_init__(self) -> None:
        if not self.version.strip():
            raise ValueError("Mem0 calibration profile version cannot be empty")
        for name in (
            "mem0_atom_tag_step",
            "mem0_relation_step",
            "vector_atom_tag_step",
            "vector_relation_step",
        ):
            value = getattr(self, name)
            if not math.isfinite(value) or value < 0.0:
                raise ValueError(f"{name} must be finite and non-negative")
        if self.vector_atom_tag_step > self.mem0_atom_tag_step / 2.0:
            raise ValueError("vector_atom_tag_step cannot exceed half the Mem0 step")
        if self.vector_relation_step > self.mem0_relation_step / 2.0:
            raise ValueError("vector_relation_step cannot exceed half the Mem0 step")
        if not 0.0 <= self.unsupported_memory_confidence <= 1.0:
            raise ValueError("unsupported_memory_confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Mem0RelationshipCalibrationResult:
    signal_count: int
    mem0_signal_count: int
    vector_signal_count: int
    atom_tag_updates: int
    tag_relation_updates: int
    vector_available: bool
    warnings: tuple[str, ...] = ()


class Mem0RelationshipCalibrationService:
    """Turn one Mem0 fact into replay-safe Mem0 and embedding graph priors.

    Mem0 supplies the structural proposal. Embeddings may add a smaller corroborating
    increment, but never manufacture a typed relation or overwrite Mem0 evidence.
    """

    def __init__(
        self,
        repository: Repository,
        *,
        embedder: Embedder | None = None,
        profile: Mem0RelationshipCalibrationProfile | None = None,
    ) -> None:
        self.repository = repository
        self.embedder = embedder
        self.profile = profile or Mem0RelationshipCalibrationProfile()
        self._label_vectors: dict[str, tuple[float, ...]] = {}

    @property
    def profile_id(self) -> str:
        embedding = (
            f"{self.embedder.provider}:{self.embedder.model}"
            if self.embedder is not None
            else "none"
        )
        return f"{self.profile.version}:embedding={embedding}"

    def calibrate_record(
        self,
        *,
        namespace: str,
        record_id: str,
        output_atom_ids: tuple[str, ...],
        support_atom_ids: tuple[str, ...] = (),
        support_confidence: float | None = None,
    ) -> Mem0RelationshipCalibrationResult:
        outputs = tuple(
            atom
            for atom in self.repository.get_atoms(output_atom_ids)
            if atom.namespace == namespace
            and atom.role is AtomRole.DERIVED
            and atom.metadata.get("source_system") == "mem0"
        )
        if not outputs:
            return Mem0RelationshipCalibrationResult(0, 0, 0, 0, 0, False)

        support = tuple(
            atom
            for atom in self.repository.get_atoms(support_atom_ids)
            if atom.namespace == namespace and atom.role is AtomRole.SOURCE
        )
        support_factor = (
            support_confidence
            if support_confidence is not None
            else self.profile.unsupported_memory_confidence
        )
        if not 0.0 <= support_factor <= 1.0:
            raise ValueError("support_confidence must be between 0 and 1")

        edges = self.repository.get_atom_tags_for_atoms(
            tuple(atom.atom_id for atom in outputs)
        )
        tags = {
            tag.tag_id: tag
            for tag in self.repository.get_tags(
                tuple(sorted({edge.tag_id for edge in edges}))
            )
        }
        edges_by_atom: dict[str, list[AtomTag]] = {}
        for edge in edges:
            if edge.tag_id in tags:
                edges_by_atom.setdefault(edge.atom_id, []).append(edge)

        atom_vectors: dict[str, tuple[float, ...]] = {}
        label_vectors: dict[str, tuple[float, ...]] = {}
        warnings: list[str] = []
        if self.embedder is not None and edges:
            try:
                atom_vectors = self._atom_vectors((*outputs, *support))
                label_vectors = self._tag_vectors(
                    tuple(sorted({tags[edge.tag_id].canonical_text for edge in edges}))
                )
            except Exception as error:  # noqa: BLE001 - Mem0 evidence remains usable
                warnings.append(f"vector_corroboration_unavailable:{type(error).__name__}")
                atom_vectors = {}
                label_vectors = {}

        proposed: list[tuple[CalibrationSignal, str, float]] = []
        for atom in outputs:
            atom_edges = sorted(
                edges_by_atom.get(atom.atom_id, ()), key=lambda edge: edge.tag_id
            )
            support_alignment = self._support_alignment(
                atom=atom,
                support=support,
                vectors=atom_vectors,
            )
            tag_alignments: dict[str, float] = {}
            for edge in atom_edges:
                proposal_confidence = edge.confidence * support_factor
                mem0_signal = self._signal(
                    namespace=namespace,
                    record_id=record_id,
                    target_type=CalibrationTarget.ATOM_TAG,
                    target_id=edge.atom_id,
                    related_id=edge.tag_id,
                    relation_type=None,
                    signal_type="mem0_atom_tag_proposal",
                    value=proposal_confidence,
                    confidence=proposal_confidence,
                    provider="mem0",
                    discriminator=atom.atom_id,
                    metadata={"evidence_role": "structural_proposal"},
                )
                proposed.append(
                    (
                        mem0_signal,
                        "atom_tag",
                        self.profile.mem0_atom_tag_step * proposal_confidence,
                    )
                )

                tag_text = tags[edge.tag_id].canonical_text
                alignment = self._similarity(
                    atom_vectors.get(atom.atom_id), label_vectors.get(tag_text)
                )
                tag_alignments[edge.tag_id] = alignment
                if atom.atom_id in atom_vectors and tag_text in label_vectors:
                    vector_confidence = (
                        proposal_confidence
                        * alignment
                        * self._support_modifier(support_alignment, bool(support))
                    )
                    vector_signal = self._signal(
                        namespace=namespace,
                        record_id=record_id,
                        target_type=CalibrationTarget.ATOM_TAG,
                        target_id=edge.atom_id,
                        related_id=edge.tag_id,
                        relation_type=None,
                        signal_type="vector_atom_tag_corroboration",
                        value=vector_confidence,
                        confidence=vector_confidence,
                        provider=f"embedding:{self.embedder.provider}",
                        discriminator=atom.atom_id,
                        metadata={
                            "evidence_role": "same_source_corroboration",
                            "embedding_model": self.embedder.model,
                            "tag_similarity": alignment,
                            "support_similarity": support_alignment,
                        },
                    )
                    proposed.append(
                        (
                            vector_signal,
                            "atom_tag",
                            self.profile.vector_atom_tag_step * vector_confidence,
                        )
                    )

            for left, right in combinations(atom_edges, 2):
                source_tag_id, target_tag_id = sorted((left.tag_id, right.tag_id))
                proposal_confidence = min(left.confidence, right.confidence) * support_factor
                mem0_signal = self._signal(
                    namespace=namespace,
                    record_id=record_id,
                    target_type=CalibrationTarget.TAG_RELATION,
                    target_id=source_tag_id,
                    related_id=target_tag_id,
                    relation_type="co_occurs",
                    signal_type="mem0_fact_relationship_proposal",
                    value=proposal_confidence,
                    confidence=proposal_confidence,
                    provider="mem0",
                    discriminator=atom.atom_id,
                    metadata={"evidence_role": "structural_proposal"},
                )
                proposed.append(
                    (
                        mem0_signal,
                        "tag_relation",
                        self.profile.mem0_relation_step * proposal_confidence,
                    )
                )

                left_alignment = tag_alignments.get(left.tag_id, 0.0)
                right_alignment = tag_alignments.get(right.tag_id, 0.0)
                if (
                    atom.atom_id in atom_vectors
                    and tags[left.tag_id].canonical_text in label_vectors
                    and tags[right.tag_id].canonical_text in label_vectors
                ):
                    pair_alignment = math.sqrt(left_alignment * right_alignment)
                    vector_confidence = (
                        proposal_confidence
                        * pair_alignment
                        * self._support_modifier(support_alignment, bool(support))
                    )
                    vector_signal = self._signal(
                        namespace=namespace,
                        record_id=record_id,
                        target_type=CalibrationTarget.TAG_RELATION,
                        target_id=source_tag_id,
                        related_id=target_tag_id,
                        relation_type="co_occurs",
                        signal_type="vector_relationship_corroboration",
                        value=vector_confidence,
                        confidence=vector_confidence,
                        provider=f"embedding:{self.embedder.provider}",
                        discriminator=atom.atom_id,
                        metadata={
                            "evidence_role": "same_source_corroboration",
                            "embedding_model": self.embedder.model,
                            "left_tag_similarity": left_alignment,
                            "right_tag_similarity": right_alignment,
                            "support_similarity": support_alignment,
                        },
                    )
                    proposed.append(
                        (
                            vector_signal,
                            "tag_relation",
                            self.profile.vector_relation_step * vector_confidence,
                        )
                    )

        if not proposed:
            return Mem0RelationshipCalibrationResult(
                0, 0, 0, 0, 0, bool(atom_vectors), tuple(warnings)
            )
        existing_ids = self.repository.get_calibration_signal_ids(
            tuple(signal.signal_id for signal, _, _ in proposed)
        )
        pending = tuple(item for item in proposed if item[0].signal_id not in existing_ids)
        if not pending:
            return Mem0RelationshipCalibrationResult(
                0, 0, 0, 0, 0, bool(atom_vectors), tuple(warnings)
            )

        atom_tag_state = {(edge.atom_id, edge.tag_id): edge for edge in edges}
        relation_ids = tuple(sorted({edge.tag_id for edge in edges}))
        relation_state = {
            (edge.source_tag_id, edge.target_tag_id, edge.relation_type): edge
            for edge in self.repository.get_tag_relations_touching(tag_ids=relation_ids)
        }
        atom_tag_deltas: dict[tuple[str, str], float] = {}
        atom_tag_signals: dict[tuple[str, str], list[CalibrationSignal]] = {}
        relation_deltas: dict[tuple[str, str, str], float] = {}
        relation_signals: dict[tuple[str, str, str], list[CalibrationSignal]] = {}
        for signal, kind, increment in pending:
            if kind == "atom_tag":
                key = (signal.target_id, signal.related_id or "")
                atom_tag_deltas[key] = atom_tag_deltas.get(key, 0.0) + increment
                atom_tag_signals.setdefault(key, []).append(signal)
            else:
                key = (
                    signal.target_id,
                    signal.related_id or "",
                    signal.relation_type or "co_occurs",
                )
                relation_deltas[key] = relation_deltas.get(key, 0.0) + increment
                relation_signals.setdefault(key, []).append(signal)

        atom_tag_updates: list[AtomTag] = []
        for key, delta in atom_tag_deltas.items():
            current = atom_tag_state[key]
            signals = atom_tag_signals[key]
            atom_tag_updates.append(
                replace(
                    current,
                    weight_raw=current.weight_raw + delta,
                    confidence=max(current.confidence, *(signal.confidence for signal in signals)),
                    evidence_sources=self._with_evidence(
                        current.evidence_sources, tuple(signal.signal_id for signal in signals)
                    ),
                    updated_at=utc_now(),
                )
            )

        relation_updates: list[TagRelation] = []
        for key, delta in relation_deltas.items():
            current = relation_state.get(key)
            signals = relation_signals[key]
            relation_updates.append(
                TagRelation(
                    source_tag_id=key[0],
                    target_tag_id=key[1],
                    relation_type=key[2],
                    weight_raw=(current.weight_raw if current else 0.0) + delta,
                    confidence=max(
                        current.confidence if current else 0.0,
                        *(signal.confidence for signal in signals),
                    ),
                    evidence_sources=self._with_evidence(
                        current.evidence_sources if current else (),
                        tuple(signal.signal_id for signal in signals),
                    ),
                    created_at=current.created_at if current else utc_now(),
                    updated_at=utc_now(),
                )
            )

        self.repository.apply_calibration_updates(
            signals=tuple(signal for signal, _, _ in pending),
            atom_tags=tuple(atom_tag_updates),
            atom_links=(),
            tag_relations=tuple(relation_updates),
        )
        return Mem0RelationshipCalibrationResult(
            signal_count=len(pending),
            mem0_signal_count=sum(signal.provider == "mem0" for signal, _, _ in pending),
            vector_signal_count=sum(
                signal.provider.startswith("embedding:") for signal, _, _ in pending
            ),
            atom_tag_updates=len(atom_tag_updates),
            tag_relation_updates=len(relation_updates),
            vector_available=bool(atom_vectors),
            warnings=tuple(warnings),
        )

    def _atom_vectors(self, atoms: tuple[Atom, ...]) -> dict[str, tuple[float, ...]]:
        assert self.embedder is not None
        unique_atoms = tuple({atom.atom_id: atom for atom in atoms}.values())
        stored = self.repository.get_embeddings(
            atom_ids=tuple(atom.atom_id for atom in unique_atoms),
            provider=self.embedder.provider,
            model=self.embedder.model,
        )
        result = {
            atom.atom_id: stored[atom.atom_id].vector
            for atom in unique_atoms
            if atom.atom_id in stored
            and stored[atom.atom_id].content_hash == atom.content_hash
        }
        missing = tuple(atom for atom in unique_atoms if atom.atom_id not in result)
        if missing:
            vectors = self.embedder.embed_documents(tuple(atom.content for atom in missing))
            if len(vectors) != len(missing):
                raise ValueError("embedder returned the wrong number of atom vectors")
            embeddings = tuple(
                AtomEmbedding(
                    atom_id=atom.atom_id,
                    provider=self.embedder.provider,
                    model=self.embedder.model,
                    dimensions=len(vector),
                    vector=vector,
                    content_hash=atom.content_hash,
                )
                for atom, vector in zip(missing, vectors, strict=True)
            )
            self.repository.upsert_embeddings(embeddings)
            result.update(
                (embedding.atom_id, embedding.vector) for embedding in embeddings
            )
        return result

    def _tag_vectors(self, labels: tuple[str, ...]) -> dict[str, tuple[float, ...]]:
        assert self.embedder is not None
        missing = tuple(label for label in labels if label not in self._label_vectors)
        if missing:
            vectors = self.embedder.embed_documents(missing)
            if len(vectors) != len(missing):
                raise ValueError("embedder returned the wrong number of tag vectors")
            self._label_vectors.update(zip(missing, vectors, strict=True))
        return {label: self._label_vectors[label] for label in labels}

    @staticmethod
    def _support_alignment(
        *,
        atom: Atom,
        support: tuple[Atom, ...],
        vectors: dict[str, tuple[float, ...]],
    ) -> float:
        output_vector = vectors.get(atom.atom_id)
        if output_vector is None or not support:
            return 0.0
        return max(
            (
                Mem0RelationshipCalibrationService._similarity(
                    output_vector, vectors.get(source.atom_id)
                )
                for source in support
            ),
            default=0.0,
        )

    @staticmethod
    def _support_modifier(alignment: float, has_support: bool) -> float:
        return 0.5 + 0.5 * alignment if has_support else 1.0

    @staticmethod
    def _similarity(
        left: tuple[float, ...] | None, right: tuple[float, ...] | None
    ) -> float:
        if left is None or right is None:
            return 0.0
        return min(1.0, max(0.0, cosine_similarity(left, right)))

    def _signal(
        self,
        *,
        namespace: str,
        record_id: str,
        target_type: CalibrationTarget,
        target_id: str,
        related_id: str,
        relation_type: str | None,
        signal_type: str,
        value: float,
        confidence: float,
        provider: str,
        discriminator: str,
        metadata: dict[str, object],
    ) -> CalibrationSignal:
        embedding_identity = (
            f"{self.embedder.provider}:{self.embedder.model}"
            if provider.startswith("embedding:") and self.embedder is not None
            else ""
        )
        signal_id = stable_id(
            "calibration",
            namespace,
            self.profile.version,
            embedding_identity,
            record_id,
            target_type.value,
            target_id,
            related_id,
            relation_type or "",
            signal_type,
            discriminator,
        )
        return CalibrationSignal(
            signal_id=signal_id,
            namespace=namespace,
            target_type=target_type,
            target_id=target_id,
            related_id=related_id,
            relation_type=relation_type,
            signal_type=signal_type,
            value=value,
            confidence=confidence,
            multiplier=1.0,
            provider=provider,
            profile_version=(
                f"{self.profile.version}:{embedding_identity}"
                if embedding_identity
                else self.profile.version
            ),
            source_reference=record_id,
            metadata=metadata,
        )

    @staticmethod
    def _with_evidence(
        existing: tuple[str, ...], signal_ids: tuple[str, ...]
    ) -> tuple[str, ...]:
        additions = tuple(f"calibration:{signal_id}" for signal_id in signal_ids)
        return tuple(dict.fromkeys((*existing, *additions)))[-50:]

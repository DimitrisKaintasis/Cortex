from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass, replace
from itertools import combinations

from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomLink,
    AtomLinkRelation,
    AtomTag,
    CalibrationSignal,
    CalibrationTarget,
    Tag,
    TagRelation,
    utc_now,
)
from data_retrieval.storage.repository import Repository

TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", re.UNICODE)
ENTITY_PATTERN = re.compile(r"\b(?:[A-Z][a-z]{2,}|[A-Z]{2,}[0-9]*)\b")
CODE_PATTERNS = tuple(
    re.compile(pattern, re.MULTILINE)
    for pattern in (
        r"^\s*(?:async\s+)?def\s+\w+",
        r"^\s*class\s+\w+",
        r"^\s*(?:export\s+)?(?:async\s+)?function\s+\w+",
        r"^\s*(?:const|let|var)\s+\w+\s*=",
        r"^\s*(?:import|from|package|module)\b",
        r"^\s*(?:func|type|struct|interface)\s+\w+",
    )
)


@dataclass(frozen=True, slots=True)
class CalibrationProfile:
    """Versioned cold-start policy reconstructed from the Tags/Cortex designs."""

    version: str = "teacher-relative-v1"
    entity_weight: float = 1.5
    code_weight: float = 2.0
    rarity_weight: float = 1.0
    low_threshold: float = 0.45
    medium_threshold: float = 0.65
    high_threshold: float = 0.80
    low_lift: float = 0.05
    medium_lift: float = 0.15
    high_lift: float = 0.30
    co_occurrence_step: float = 0.10
    hierarchy_weight: float = 0.25


@dataclass(frozen=True, slots=True)
class CalibrationResult:
    document_id: str
    signal_count: int
    atom_tag_updates: int
    atom_link_updates: int
    tag_relation_updates: int
    idempotent: bool


class TeacherCalibrationService:
    """Initialize native graph weights from deterministic, replayable evidence."""

    def __init__(
        self,
        repository: Repository,
        profile: CalibrationProfile | None = None,
    ) -> None:
        self.repository = repository
        self.profile = profile or CalibrationProfile()

    def calibrate_document(
        self,
        document_id: str,
        *,
        provider: str = "deterministic-teachers",
        multiplier: float = 1.0,
        source_reference: str | None = None,
        atom_ids: tuple[str, ...] | None = None,
    ) -> CalibrationResult:
        if multiplier <= 0.0:
            raise ValueError("multiplier must be positive")
        document = self.repository.get_document(document_id)
        if document is None:
            raise ValueError(f"unknown document_id: {document_id}")
        if atom_ids is None:
            atoms = self.repository.get_atoms_for_document(document_id)
        else:
            atoms = self.repository.get_atoms(atom_ids)
            if len(atoms) != len(set(atom_ids)) or any(
                atom.document_id != document_id for atom in atoms
            ):
                raise ValueError("all calibrated atoms must belong to the document")
        scores = self._teacher_scores(atoms)
        atom_edges: dict[str, tuple[AtomTag, ...]] = {atom.atom_id: () for atom in atoms}
        grouped_edges: dict[str, list[AtomTag]] = {atom.atom_id: [] for atom in atoms}
        for edge in self.repository.get_atom_tags_for_atoms(
            tuple(atom.atom_id for atom in atoms)
        ):
            grouped_edges[edge.atom_id].append(edge)
        atom_edges.update(
            (atom_id, tuple(edges)) for atom_id, edges in grouped_edges.items()
        )
        tag_ids = tuple(
            sorted({edge.tag_id for edges in atom_edges.values() for edge in edges})
        )
        tags_by_id = {tag.tag_id: tag for tag in self.repository.get_tags(tag_ids)}

        proposed_signals: list[CalibrationSignal] = []
        signal_work: list[tuple[str, object]] = []
        for atom in atoms:
            components = scores[atom.atom_id]
            composite = self._composite(components)
            for signal_type, value in components.items():
                proposed_signals.append(
                    self._signal(
                        namespace=document.namespace,
                        target_type=CalibrationTarget.ATOM,
                        target_id=atom.atom_id,
                        signal_type=signal_type,
                        value=value,
                        confidence=value,
                        provider="deterministic-teachers",
                        multiplier=1.0,
                        source_reference=source_reference,
                    )
                )
            proposed_signals.append(
                self._signal(
                    namespace=document.namespace,
                    target_type=CalibrationTarget.ATOM,
                    target_id=atom.atom_id,
                    signal_type="teacher_composite",
                    value=composite,
                    confidence=composite,
                    provider=provider,
                    multiplier=multiplier,
                    source_reference=source_reference,
                )
            )
            for edge in atom_edges[atom.atom_id]:
                signal = self._signal(
                    namespace=document.namespace,
                    target_type=CalibrationTarget.ATOM_TAG,
                    target_id=edge.atom_id,
                    related_id=edge.tag_id,
                    signal_type="initial_weight",
                    value=composite,
                    confidence=composite,
                    provider=provider,
                    multiplier=multiplier,
                    source_reference=source_reference,
                )
                proposed_signals.append(signal)
                signal_work.append((signal.signal_id, ("atom_tag", edge, composite)))
            ordered_edges = sorted(atom_edges[atom.atom_id], key=lambda edge: edge.tag_id)
            for left, right in combinations(ordered_edges, 2):
                source_tag_id, target_tag_id = sorted((left.tag_id, right.tag_id))
                confidence = min(left.confidence, right.confidence, max(composite, 0.25))
                signal = self._signal(
                    namespace=document.namespace,
                    target_type=CalibrationTarget.TAG_RELATION,
                    target_id=source_tag_id,
                    related_id=target_tag_id,
                    relation_type="co_occurs",
                    signal_type="same_atom_co_occurrence",
                    value=composite,
                    confidence=confidence,
                    provider=provider,
                    multiplier=multiplier,
                    source_reference=source_reference,
                    discriminator=atom.atom_id,
                )
                proposed_signals.append(signal)
                signal_work.append((signal.signal_id, ("co_occurs", signal)))

        ordered_atoms = sorted(atoms, key=lambda atom: atom.position)
        for index, atom in enumerate(ordered_atoms):
            for distance in (1, 2):
                if index + distance >= len(ordered_atoms):
                    continue
                neighbor = ordered_atoms[index + distance]
                signal = self._signal(
                    namespace=document.namespace,
                    target_type=CalibrationTarget.ATOM_LINK,
                    target_id=atom.atom_id,
                    related_id=neighbor.atom_id,
                    relation_type=AtomLinkRelation.ADJACENT_TO.value,
                    signal_type="source_adjacency",
                    value=1.0 if distance == 1 else 0.5,
                    confidence=1.0,
                    provider="deterministic-teachers",
                    multiplier=1.0,
                    source_reference=document.source,
                    discriminator=str(distance),
                )
                proposed_signals.append(signal)
                signal_work.append((signal.signal_id, ("adjacency", signal, distance)))

        hierarchy_signals = self._hierarchy_signals(
            namespace=document.namespace,
            tags=tuple(tags_by_id.values()),
            source_reference=source_reference,
        )
        proposed_signals.extend(hierarchy_signals)
        signal_work.extend(
            (signal.signal_id, ("hierarchy", signal)) for signal in hierarchy_signals
        )

        existing_ids = self.repository.get_calibration_signal_ids(
            tuple(signal.signal_id for signal in proposed_signals)
        )
        new_signals = tuple(
            signal for signal in proposed_signals if signal.signal_id not in existing_ids
        )
        if not new_signals:
            return CalibrationResult(
                document_id=document_id,
                signal_count=0,
                atom_tag_updates=0,
                atom_link_updates=0,
                tag_relation_updates=0,
                idempotent=True,
            )
        new_ids = {signal.signal_id for signal in new_signals}

        atom_tag_updates: dict[tuple[str, str], AtomTag] = {}
        atom_link_updates: dict[tuple[str, str, AtomLinkRelation], AtomLink] = {}
        relation_keys = tuple(tags_by_id)
        relation_state = {
            (edge.source_tag_id, edge.target_tag_id, edge.relation_type): edge
            for edge in self.repository.get_tag_relations_touching(tag_ids=relation_keys)
        }
        for signal_id, work in signal_work:
            if signal_id not in new_ids:
                continue
            kind = work[0]
            if kind == "atom_tag":
                edge = work[1]
                composite = work[2]
                assert isinstance(edge, AtomTag)
                assert isinstance(composite, float)
                lift = self._lift(composite) * multiplier
                atom_tag_updates[(edge.atom_id, edge.tag_id)] = replace(
                    edge,
                    weight_raw=max(edge.weight_raw, 1.0 + lift),
                    confidence=max(edge.confidence, composite),
                    updated_at=utc_now(),
                )
                continue
            if kind == "adjacency":
                signal = work[1]
                distance = work[2]
                assert isinstance(signal, CalibrationSignal)
                assert isinstance(distance, int)
                relation = AtomLinkRelation.ADJACENT_TO
                atom_link_updates[(signal.target_id, signal.related_id or "", relation)] = (
                    AtomLink(
                        from_atom_id=signal.target_id,
                        to_atom_id=signal.related_id or "",
                        relation=relation,
                        weight_raw=signal.value,
                        confidence=signal.confidence,
                        evidence_sources=(f"calibration:{signal.signal_id}",),
                        metadata={"structural": True, "distance": distance},
                    )
                )
                continue
            signal = work[1]
            assert isinstance(signal, CalibrationSignal)
            relation_type = "co_occurs" if kind == "co_occurs" else "parent_of"
            key = (signal.target_id, signal.related_id or "", relation_type)
            current = relation_state.get(key)
            if kind == "co_occurs":
                increment = self.profile.co_occurrence_step * signal.confidence * signal.multiplier
                weight = (current.weight_raw if current else 0.0) + increment
            else:
                weight = max(current.weight_raw if current else 0.0, self.profile.hierarchy_weight)
            relation_state[key] = TagRelation(
                source_tag_id=key[0],
                target_tag_id=key[1],
                relation_type=relation_type,
                weight_raw=weight,
                confidence=max(current.confidence if current else 0.0, signal.confidence),
                evidence_sources=self._with_evidence(
                    current.evidence_sources if current else (), signal_id
                ),
                created_at=current.created_at if current else utc_now(),
                updated_at=utc_now(),
            )

        changed_relations = tuple(
            relation_state[key]
            for key in sorted(relation_state)
            if any(
                work_signal_id in new_ids
                and work[0] in {"co_occurs", "hierarchy"}
                and isinstance(work[1], CalibrationSignal)
                and (work[1].target_id, work[1].related_id or "", relation_state[key].relation_type)
                == key
                for work_signal_id, work in signal_work
            )
        )
        self.repository.apply_calibration_updates(
            signals=new_signals,
            atom_tags=tuple(atom_tag_updates.values()),
            atom_links=tuple(atom_link_updates.values()),
            tag_relations=changed_relations,
        )
        return CalibrationResult(
            document_id=document_id,
            signal_count=len(new_signals),
            atom_tag_updates=len(atom_tag_updates),
            atom_link_updates=len(atom_link_updates),
            tag_relation_updates=len(changed_relations),
            idempotent=False,
        )

    def calibrate_document_batched(
        self,
        document_id: str,
        *,
        batch_size: int = 1_000,
        provider: str = "deterministic-teachers",
        multiplier: float = 1.0,
        source_reference: str | None = None,
    ) -> CalibrationResult:
        """Calibrate arbitrarily large documents while preserving cross-batch adjacency."""

        if batch_size < 3:
            raise ValueError("batch_size must be at least three")
        totals = {
            "signals": 0,
            "atom_tags": 0,
            "atom_links": 0,
            "tag_relations": 0,
        }
        changed = False
        carry: tuple[str, ...] = ()
        for atom_ids in self.repository.iter_atom_ids_for_document(
            document_id=document_id, batch_size=batch_size
        ):
            selected = tuple(dict.fromkeys((*carry, *atom_ids)))
            result = self.calibrate_document(
                document_id,
                provider=provider,
                multiplier=multiplier,
                source_reference=source_reference,
                atom_ids=selected,
            )
            changed = changed or not result.idempotent
            totals["signals"] += result.signal_count
            totals["atom_tags"] += result.atom_tag_updates
            totals["atom_links"] += result.atom_link_updates
            totals["tag_relations"] += result.tag_relation_updates
            carry = atom_ids[-2:]
        return CalibrationResult(
            document_id=document_id,
            signal_count=totals["signals"],
            atom_tag_updates=totals["atom_tags"],
            atom_link_updates=totals["atom_links"],
            tag_relation_updates=totals["tag_relations"],
            idempotent=not changed,
        )

    def _teacher_scores(self, atoms: tuple[Atom, ...]) -> dict[str, dict[str, float]]:
        token_sets = [set(TOKEN_PATTERN.findall(atom.content.casefold())) for atom in atoms]
        frequencies = Counter(token for tokens in token_sets for token in tokens)
        maximum_idf = math.log1p(max(1, len(atoms))) + 1.0
        result: dict[str, dict[str, float]] = {}
        for atom, tokens in zip(atoms, token_sets, strict=True):
            token_count = max(1, len(TOKEN_PATTERN.findall(atom.content)))
            entity_count = len(ENTITY_PATTERN.findall(atom.content))
            entity_density = min(1.0, entity_count / max(1.0, token_count / 8.0))
            structure_hits = sum(len(pattern.findall(atom.content)) for pattern in CODE_PATTERNS)
            code_structure = min(1.0, structure_hits / 3.0)
            rarity = (
                sum(
                    math.log((1 + len(atoms)) / (1 + frequencies[token])) + 1.0
                    for token in tokens
                )
                / max(1, len(tokens))
                / maximum_idf
            )
            result[atom.atom_id] = {
                "entity_density": entity_density,
                "code_structure": code_structure,
                "bm25_uniqueness": min(1.0, rarity),
            }
        return result

    def _composite(self, scores: dict[str, float]) -> float:
        weighted = (
            self.profile.entity_weight * scores["entity_density"]
            + self.profile.code_weight * scores["code_structure"]
            + self.profile.rarity_weight * scores["bm25_uniqueness"]
        )
        total = self.profile.entity_weight + self.profile.code_weight + self.profile.rarity_weight
        return min(1.0, weighted / total)

    def _lift(self, value: float) -> float:
        if value >= self.profile.high_threshold:
            return self.profile.high_lift
        if value >= self.profile.medium_threshold:
            return self.profile.medium_lift
        if value >= self.profile.low_threshold:
            return self.profile.low_lift
        return 0.0

    def _hierarchy_signals(
        self,
        *,
        namespace: str,
        tags: tuple[Tag, ...],
        source_reference: str | None,
    ) -> tuple[CalibrationSignal, ...]:
        signals: list[CalibrationSignal] = []
        for broad in tags:
            broad_tokens = set(broad.canonical_text.split())
            if len(broad_tokens) != 1:
                continue
            for specific in tags:
                specific_tokens = set(specific.canonical_text.split())
                if broad.tag_id == specific.tag_id or len(specific_tokens) <= 1:
                    continue
                if not broad_tokens.issubset(specific_tokens):
                    continue
                signals.append(
                    self._signal(
                        namespace=namespace,
                        target_type=CalibrationTarget.TAG_RELATION,
                        target_id=broad.tag_id,
                        related_id=specific.tag_id,
                        relation_type="parent_of",
                        signal_type="lexical_tag_hierarchy",
                        value=0.85,
                        confidence=0.85,
                        provider="deterministic-teachers",
                        multiplier=1.0,
                        source_reference=source_reference,
                    )
                )
        return tuple(signals)

    def _signal(
        self,
        *,
        namespace: str,
        target_type: CalibrationTarget,
        target_id: str,
        signal_type: str,
        value: float,
        confidence: float,
        provider: str,
        multiplier: float,
        source_reference: str | None,
        related_id: str | None = None,
        relation_type: str | None = None,
        discriminator: str = "",
    ) -> CalibrationSignal:
        signal_id = stable_id(
            "calibration",
            namespace,
            self.profile.version,
            provider,
            target_type.value,
            target_id,
            related_id or "",
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
            multiplier=multiplier,
            provider=provider,
            profile_version=self.profile.version,
            source_reference=source_reference,
        )

    @staticmethod
    def _with_evidence(existing: tuple[str, ...], signal_id: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*existing, f"calibration:{signal_id}")))[-50:]

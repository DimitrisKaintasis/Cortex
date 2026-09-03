from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    AtomRole,
    CalibrationSignal,
    CalibrationTarget,
    Document,
    IngestionBundle,
    PayloadModality,
    utc_now,
)
from data_retrieval.mem0.provenance import PROVENANCE_FIELD
from data_retrieval.storage.repository import Repository

MEM0_ENTITY_PROFILE = "mem0-entity-graph-v1"


@dataclass(frozen=True, slots=True)
class Mem0Entity:
    """One batch-scoped Mem0 entity with exact native evidence lineage."""

    entity_id: str
    name: str
    support_atom_ids: tuple[str, ...]
    entity_type: str | None = None
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.entity_id.strip():
            raise ValueError("Mem0 entity_id cannot be empty")
        if not self.name.strip():
            raise ValueError("Mem0 entity name cannot be empty")
        if not self.support_atom_ids:
            raise ValueError("Mem0 entities require at least one supporting source atom")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Mem0 entity confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Mem0EntityRelationship:
    """A typed Mem0 proposal between two entities from the same extraction batch."""

    relationship_id: str
    source_entity_id: str
    target_entity_id: str
    predicate: str
    support_atom_ids: tuple[str, ...]
    confidence: float = 1.0
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("relationship_id", self.relationship_id),
            ("source_entity_id", self.source_entity_id),
            ("target_entity_id", self.target_entity_id),
            ("predicate", self.predicate),
        ):
            if not value.strip():
                raise ValueError(f"Mem0 relationship {name} cannot be empty")
        if self.source_entity_id == self.target_entity_id:
            raise ValueError("Mem0 entity relationships require distinct endpoints")
        if not self.support_atom_ids:
            raise ValueError("Mem0 entity relationships require source-atom support")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("Mem0 relationship confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Mem0ProcessResult:
    """Normalized result at the Mem0 processor boundary.

    Memories remain available for diagnostics and legacy export workflows. The live
    Cortex bootstrap imports only entities, relationships, and their source lineage.
    """

    memories: tuple[dict[str, Any], ...] = ()
    entities: tuple[Mem0Entity, ...] = ()
    relationships: tuple[Mem0EntityRelationship, ...] = ()
    relationships_quarantined: int = 0
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Mem0EntityImportResult:
    entity_atom_ids: dict[str, str]
    entities_imported: int
    entity_support_links_created: int
    entity_relationship_links_created: int
    calibration_signals_created: int


def normalize_mem0_response(
    response: Mapping[str, Any],
    *,
    messages: tuple[dict[str, str], ...],
    source_atom_ids: tuple[str, ...],
    batch_id: str,
) -> Mem0ProcessResult:
    """Convert Mem0's version-dependent graph payload into a strict native contract.

    Only the opt-in provenance payload is accepted. Normal Mem0 graph additions do
    not contain enough evidence lineage to import safely, so they remain in Mem0 but
    are not projected into Cortex.
    """

    if len(messages) != len(source_atom_ids):
        raise ValueError("Mem0 messages and source_atom_ids must have equal lengths")
    raw_memories = response.get("results", ())
    if not isinstance(raw_memories, list | tuple):
        raise ValueError("Mem0 add response.results must be a list")
    memories = tuple(dict(item) for item in raw_memories if isinstance(item, Mapping))

    relation_payload = response.get("relations", {})
    raw_relationships: tuple[Mapping[str, Any], ...] = ()
    if isinstance(relation_payload, Mapping):
        raw_value = relation_payload.get(PROVENANCE_FIELD, ())
        if isinstance(raw_value, list | tuple):
            raw_relationships = tuple(item for item in raw_value if isinstance(item, Mapping))
    entity_state: dict[str, dict[str, Any]] = {}
    relationships: dict[str, Mem0EntityRelationship] = {}
    warnings: list[str] = []
    quarantined = 0

    for index, item in enumerate(raw_relationships):
        source_name = _text(item, "source", "source_entity", "from")
        target_name = _text(item, "target", "destination", "target_entity", "to")
        predicate = _text(item, "relationship", "relation", "predicate", "type")
        if not source_name or not target_name or not predicate:
            warnings.append("mem0_relationship_invalid_shape")
            quarantined += 1
            continue

        relationship_support = _ids(item.get("evidence_source_ids"))
        source_occurrences = _ids(item.get("source_evidence_source_ids"))
        target_occurrences = _ids(item.get("destination_evidence_source_ids"))
        declared = (*relationship_support, *source_occurrences, *target_occurrences)
        if (
            item.get("provenance_valid") is not True
            or not relationship_support
            or not source_occurrences
            or not target_occurrences
            or any(atom_id not in source_atom_ids for atom_id in declared)
        ):
            warnings.append("mem0_relationship_invalid_provenance")
            quarantined += 1
            continue

        source_key = _entity_key(source_name)
        target_key = _entity_key(target_name)
        if source_key == target_key:
            warnings.append("mem0_relationship_self_reference")
            quarantined += 1
            continue
        source_id = stable_id("mem0-entity", batch_id, source_key)
        target_id = stable_id("mem0-entity", batch_id, target_key)
        _merge_entity(
            entity_state,
            entity_id=source_id,
            name=_display_name(source_name),
            support_atom_ids=source_occurrences,
            entity_type=_optional_text(item, "source_type"),
            confidence=_confidence(item),
        )
        _merge_entity(
            entity_state,
            entity_id=target_id,
            name=_display_name(target_name),
            support_atom_ids=target_occurrences,
            entity_type=_optional_text(item, "target_type", "destination_type"),
            confidence=_confidence(item),
        )
        relationship_id = str(item.get("id") or item.get("relationship_id") or "").strip()
        if not relationship_id:
            relationship_id = stable_id(
                "mem0-entity-relationship",
                batch_id,
                source_id,
                predicate.casefold(),
                target_id,
                *relationship_support,
                str(index),
            )
        relationships[relationship_id] = Mem0EntityRelationship(
            relationship_id=relationship_id,
            source_entity_id=source_id,
            target_entity_id=target_id,
            predicate=predicate,
            support_atom_ids=relationship_support,
            confidence=_confidence(item),
            metadata={"raw_provider": "mem0"},
        )

    entities = tuple(
        Mem0Entity(
            entity_id=entity_id,
            name=str(values["name"]),
            support_atom_ids=tuple(sorted(values["support_atom_ids"])),
            entity_type=values["entity_type"],
            confidence=float(values["confidence"]),
            metadata={"raw_provider": "mem0"},
        )
        for entity_id, values in sorted(entity_state.items())
    )
    return Mem0ProcessResult(
        memories=memories,
        entities=entities,
        relationships=tuple(relationships.values()),
        relationships_quarantined=quarantined,
        warnings=tuple(dict.fromkeys(warnings)),
    )


class Mem0EntityImportService:
    """Persist Mem0 entities as private derived atoms without copying source tags."""

    profile_id = MEM0_ENTITY_PROFILE

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def import_graph(
        self,
        *,
        namespace: str,
        batch_id: str,
        entities: tuple[Mem0Entity, ...],
        relationships: tuple[Mem0EntityRelationship, ...],
    ) -> Mem0EntityImportResult:
        if not namespace.strip():
            raise ValueError("namespace cannot be empty")
        entities_by_id = {entity.entity_id: entity for entity in entities}
        if len(entities_by_id) != len(entities):
            raise ValueError("duplicate Mem0 entity_id in batch")
        self._validate_source_lineage(
            namespace=namespace,
            support_atom_ids=tuple(
                dict.fromkeys(
                    atom_id
                    for item in (*entities, *relationships)
                    for atom_id in item.support_atom_ids
                )
            ),
        )
        for relationship in relationships:
            if (
                relationship.source_entity_id not in entities_by_id
                or relationship.target_entity_id not in entities_by_id
            ):
                raise ValueError("Mem0 relationship references an unknown batch entity")

        entity_atom_ids = {
            entity.entity_id: stable_id("mem0-entity-atom", namespace, batch_id, entity.entity_id)
            for entity in entities
        }
        entities_imported = self._persist_entities(
            namespace=namespace,
            batch_id=batch_id,
            entities=entities,
            entity_atom_ids=entity_atom_ids,
        )
        support_signals, support_links = self._support_updates(
            namespace=namespace,
            batch_id=batch_id,
            entities=entities,
            entity_atom_ids=entity_atom_ids,
        )
        relation_signals, relation_links = self._relationship_updates(
            namespace=namespace,
            relationships=relationships,
            entity_atom_ids=entity_atom_ids,
        )
        signals = (*support_signals, *relation_signals)
        if signals:
            self.repository.apply_calibration_updates(
                signals=signals,
                atom_tags=(),
                atom_links=(*support_links, *relation_links),
                tag_relations=(),
            )
        return Mem0EntityImportResult(
            entity_atom_ids=entity_atom_ids,
            entities_imported=entities_imported,
            entity_support_links_created=len(support_links),
            entity_relationship_links_created=len(relation_links),
            calibration_signals_created=len(signals),
        )

    def _validate_source_lineage(
        self, *, namespace: str, support_atom_ids: tuple[str, ...]
    ) -> None:
        sources = {
            atom.atom_id: atom for atom in self.repository.get_atoms(support_atom_ids)
        }
        invalid = tuple(
            atom_id
            for atom_id in support_atom_ids
            if atom_id not in sources
            or sources[atom_id].namespace != namespace
            or sources[atom_id].role is not AtomRole.SOURCE
        )
        if invalid:
            raise ValueError(
                "Mem0 entity support must reference source-role atoms in the namespace: "
                + ", ".join(invalid)
            )

    def _persist_entities(
        self,
        *,
        namespace: str,
        batch_id: str,
        entities: tuple[Mem0Entity, ...],
        entity_atom_ids: dict[str, str],
    ) -> int:
        if not entities:
            return 0
        payload = json.dumps(
            [
                {
                    "entity_id": entity.entity_id,
                    "name": entity.name,
                    "support_atom_ids": entity.support_atom_ids,
                }
                for entity in entities
            ],
            sort_keys=True,
            separators=(",", ":"),
        )
        document_id = stable_id("mem0-entity-document", namespace, batch_id)
        if self.repository.get_document(document_id) is not None:
            return 0
        source = f"mem0-entities:{batch_id}"
        document = Document(
            document_id=document_id,
            namespace=namespace,
            source=source,
            content_hash=content_hash(payload),
            metadata={
                "source_system": "mem0",
                "mem0_object_type": "entity_batch",
                "bootstrap_batch_id": batch_id,
            },
        )
        atoms: list[Atom] = []
        cursor = 0
        for position, entity in enumerate(sorted(entities, key=lambda item: item.entity_id)):
            atoms.append(
                Atom(
                    atom_id=entity_atom_ids[entity.entity_id],
                    document_id=document_id,
                    namespace=namespace,
                    position=position,
                    char_start=cursor,
                    char_end=cursor + len(entity.name),
                    content=entity.name,
                    content_hash=content_hash(entity.name),
                    kind=AtomKind.SOURCE,
                    role=AtomRole.DERIVED,
                    modality=PayloadModality.TEXT,
                    metadata={
                        **entity.metadata,
                        "source": source,
                        "source_system": "mem0",
                        "mem0_object_type": "entity_mention",
                        "mem0_entity_id": entity.entity_id,
                        "mem0_entity_type": entity.entity_type,
                        "support_atom_ids": list(entity.support_atom_ids),
                        "bootstrap_batch_id": batch_id,
                    },
                )
            )
            cursor += len(entity.name) + 1
        self.repository.persist_ingestion(
            IngestionBundle(
                document=document,
                atoms=tuple(atoms),
                tags=(),
                atom_tags=(),
            )
        )
        return len(atoms)

    def _support_updates(
        self,
        *,
        namespace: str,
        batch_id: str,
        entities: tuple[Mem0Entity, ...],
        entity_atom_ids: dict[str, str],
    ) -> tuple[tuple[CalibrationSignal, ...], tuple[AtomLink, ...]]:
        proposed: dict[str, tuple[CalibrationSignal, AtomLink]] = {}
        for entity in entities:
            entity_atom_id = entity_atom_ids[entity.entity_id]
            for source_atom_id in entity.support_atom_ids:
                signal_id = stable_id(
                    "calibration",
                    namespace,
                    MEM0_ENTITY_PROFILE,
                    "support",
                    entity_atom_id,
                    source_atom_id,
                )
                proposed[signal_id] = (
                    CalibrationSignal(
                        signal_id=signal_id,
                        namespace=namespace,
                        target_type=CalibrationTarget.ATOM_LINK,
                        target_id=entity_atom_id,
                        related_id=source_atom_id,
                        relation_type=AtomLinkRelation.SUPPORTED_BY.value,
                        signal_type="mem0_entity_occurrence",
                        value=1.0,
                        confidence=entity.confidence,
                        multiplier=1.0,
                        provider="mem0",
                        profile_version=MEM0_ENTITY_PROFILE,
                        source_reference=batch_id,
                        metadata={"copies_source_tags": False},
                    ),
                    AtomLink(
                        from_atom_id=entity_atom_id,
                        to_atom_id=source_atom_id,
                        relation=AtomLinkRelation.SUPPORTED_BY,
                        weight_raw=1.0,
                        confidence=entity.confidence,
                        evidence_sources=(f"calibration:{signal_id}",),
                        metadata={
                            "source_system": "mem0",
                            "lineage": True,
                            "support_scope": "entity_occurrence",
                            "copies_source_tags": False,
                        },
                    ),
                )
        existing = self.repository.get_calibration_signal_ids(tuple(proposed))
        pending = tuple(pair for key, pair in proposed.items() if key not in existing)
        return (
            tuple(signal for signal, _ in pending),
            tuple(link for _, link in pending),
        )

    def _relationship_updates(
        self,
        *,
        namespace: str,
        relationships: tuple[Mem0EntityRelationship, ...],
        entity_atom_ids: dict[str, str],
    ) -> tuple[tuple[CalibrationSignal, ...], tuple[AtomLink, ...]]:
        proposed: dict[str, tuple[CalibrationSignal, Mem0EntityRelationship]] = {}
        for relationship in relationships:
            source_atom_id = entity_atom_ids[relationship.source_entity_id]
            target_atom_id = entity_atom_ids[relationship.target_entity_id]
            signal_id = stable_id(
                "calibration",
                namespace,
                MEM0_ENTITY_PROFILE,
                relationship.relationship_id,
                source_atom_id,
                relationship.predicate.casefold(),
                target_atom_id,
            )
            proposed[signal_id] = (
                CalibrationSignal(
                    signal_id=signal_id,
                    namespace=namespace,
                    target_type=CalibrationTarget.ATOM_LINK,
                    target_id=source_atom_id,
                    related_id=target_atom_id,
                    relation_type=AtomLinkRelation.MEM0_ENTITY_RELATION.value,
                    signal_type="mem0_entity_relationship",
                    value=relationship.confidence,
                    confidence=relationship.confidence,
                    multiplier=1.0,
                    provider="mem0",
                    profile_version=MEM0_ENTITY_PROFILE,
                    source_reference=relationship.relationship_id,
                    metadata={
                        "predicate": relationship.predicate,
                        "support_atom_ids": list(relationship.support_atom_ids),
                        "copies_source_tags": False,
                    },
                ),
                relationship,
            )
        existing_ids = self.repository.get_calibration_signal_ids(tuple(proposed))
        pending = {key: pair for key, pair in proposed.items() if key not in existing_ids}
        if not pending:
            return (), ()

        involved = tuple(
            dict.fromkeys(
                entity_atom_ids[entity_id]
                for _, relationship in pending.values()
                for entity_id in (
                    relationship.source_entity_id,
                    relationship.target_entity_id,
                )
            )
        )
        current = {
            (link.from_atom_id, link.to_atom_id): link
            for link in self.repository.get_atom_links_touching(
                atom_ids=involved,
                relation=AtomLinkRelation.MEM0_ENTITY_RELATION,
            )
        }
        grouped: dict[tuple[str, str], list[tuple[str, Mem0EntityRelationship]]] = {}
        for signal_id, (_, relationship) in pending.items():
            key = (
                entity_atom_ids[relationship.source_entity_id],
                entity_atom_ids[relationship.target_entity_id],
            )
            grouped.setdefault(key, []).append((signal_id, relationship))

        links: list[AtomLink] = []
        for (source_atom_id, target_atom_id), additions in sorted(grouped.items()):
            previous = current.get((source_atom_id, target_atom_id))
            predicates = {
                str(value)
                for value in (previous.metadata.get("predicates", ()) if previous else ())
            }
            support_atom_ids = {
                str(value)
                for value in (previous.metadata.get("support_atom_ids", ()) if previous else ())
            }
            evidence_sources = set(previous.evidence_sources if previous else ())
            increment = 0.0
            confidence = previous.confidence if previous else 0.0
            for signal_id, relationship in additions:
                predicates.add(relationship.predicate)
                support_atom_ids.update(relationship.support_atom_ids)
                evidence_sources.add(f"calibration:{signal_id}")
                increment += relationship.confidence
                confidence = max(confidence, relationship.confidence)
            now = utc_now()
            links.append(
                AtomLink(
                    from_atom_id=source_atom_id,
                    to_atom_id=target_atom_id,
                    relation=AtomLinkRelation.MEM0_ENTITY_RELATION,
                    weight_raw=(previous.weight_raw if previous else 0.0) + increment,
                    confidence=confidence,
                    evidence_sources=tuple(sorted(evidence_sources)),
                    created_at=previous.created_at if previous else now,
                    updated_at=now,
                    metadata={
                        **(previous.metadata if previous else {}),
                        "source_system": "mem0",
                        "predicates": sorted(predicates),
                        "support_atom_ids": sorted(support_atom_ids),
                        "copies_source_tags": False,
                    },
                )
            )
        return tuple(signal for signal, _ in pending.values()), tuple(links)


def _merge_entity(
    state: dict[str, dict[str, Any]],
    *,
    entity_id: str,
    name: str,
    support_atom_ids: tuple[str, ...],
    entity_type: str | None,
    confidence: float,
) -> None:
    values = state.setdefault(
        entity_id,
        {
            "name": name,
            "support_atom_ids": set(),
            "entity_type": entity_type,
            "confidence": confidence,
        },
    )
    values["support_atom_ids"].update(support_atom_ids)
    values["confidence"] = max(float(values["confidence"]), confidence)
    if values["entity_type"] is None and entity_type is not None:
        values["entity_type"] = entity_type


def _entity_key(value: str) -> str:
    return " ".join(_display_name(value).casefold().split())


def _display_name(value: str) -> str:
    return value.strip().replace("_", " ")


def _text(item: Mapping[str, Any], *keys: str) -> str:
    value = _optional_text(item, *keys)
    return value or ""


def _optional_text(item: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(dict.fromkeys(str(item) for item in value if str(item).strip()))


def _confidence(item: Mapping[str, Any]) -> float:
    raw = item.get("confidence", item.get("score", 1.0))
    value = float(raw)
    if not math.isfinite(value) or not 0.0 <= value <= 1.0:
        raise ValueError("Mem0 relationship confidence must be between 0 and 1")
    return value

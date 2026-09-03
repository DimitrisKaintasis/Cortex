from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    AtomLink,
    AtomLinkRelation,
    AtomRole,
    CalibrationSignal,
    CalibrationTarget,
    PayloadModality,
)
from data_retrieval.retrieval.embedding import Embedder
from data_retrieval.retrieval.models import AtomEmbedding
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer
from data_retrieval.tagging.proposals import TagProposer

MEM0_BATCH_LIMIT = 500
MEM0_NEAR_DUPLICATE_THRESHOLD = 0.92
MEM0_LEARNING_MULTIPLIER = 2.0


@dataclass(frozen=True, slots=True)
class Mem0Record:
    record_id: str
    content: str
    tags: tuple[str, ...] = ()
    occurred_at: datetime | None = None
    conflicts_with: tuple[str, ...] = ()
    support_atom_ids: tuple[str, ...] = ()
    batch_atom_ids: tuple[str, ...] = ()
    support_method: str | None = None
    support_confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.record_id.strip():
            raise ValueError("Mem0 record_id cannot be empty")
        if not self.content.strip():
            raise ValueError("Mem0 content cannot be empty")
        if self.occurred_at is not None and self.occurred_at.tzinfo is None:
            raise ValueError("Mem0 occurred_at must include a timezone")
        if self.support_confidence is not None and not 0.0 <= self.support_confidence <= 1.0:
            raise ValueError("Mem0 support_confidence must be between 0 and 1")


@dataclass(frozen=True, slots=True)
class Mem0ImportResult:
    imported_record_ids: tuple[str, ...]
    exact_duplicate_record_ids: tuple[str, ...]
    semantic_duplicate_record_ids: tuple[str, ...]
    record_atom_ids: dict[str, tuple[str, ...]]
    conflict_links_created: int
    source_lineage_links_created: int
    calibration_signals_created: int
    calibration_warnings: tuple[str, ...] = ()


class Mem0ImportService:
    """Translate Mem0 exports into canonical native atoms and boosted graph evidence."""

    def __init__(
        self,
        repository: Repository,
        *,
        embedder: Embedder | None = None,
        tag_proposer: TagProposer | None = None,
        near_duplicate_threshold: float = MEM0_NEAR_DUPLICATE_THRESHOLD,
    ) -> None:
        if not 0.0 <= near_duplicate_threshold <= 1.0:
            raise ValueError("near_duplicate_threshold must be between 0 and 1")
        self.repository = repository
        self.embedder = embedder
        self.tag_proposer = tag_proposer
        self.near_duplicate_threshold = near_duplicate_threshold

    @property
    def profile_id(self) -> str:
        return "mem0-record-import-v3"

    def import_records(
        self,
        *,
        namespace: str,
        records: tuple[Mem0Record, ...],
    ) -> Mem0ImportResult:
        if not namespace.strip():
            raise ValueError("namespace cannot be empty")
        if len(records) > MEM0_BATCH_LIMIT:
            raise ValueError(f"Mem0 imports are limited to {MEM0_BATCH_LIMIT} records per batch")

        imported: list[str] = []
        exact_duplicates: list[str] = []
        semantic_duplicates: list[str] = []
        record_atom_ids: dict[str, tuple[str, ...]] = {}
        calibration_count = 0
        calibration_warnings: list[str] = []
        source_lineage_count = 0
        seen_record_ids: set[str] = set()

        for record in records:
            if record.record_id in seen_record_ids:
                raise ValueError(f"duplicate Mem0 record_id in batch: {record.record_id}")
            seen_record_ids.add(record.record_id)
        for record in records:
            self._validated_support_ids(namespace=namespace, record=record)

        for record in records:
            record_hash = content_hash(record.content)
            exact_documents = self.repository.find_documents_by_content_hash(
                namespace=namespace, content_hash=record_hash
            )
            exact_atoms = self.repository.find_atoms_by_content_hash(
                namespace=namespace, content_hash=content_hash(record.content.strip())
            )
            if exact_documents:
                atom_ids = tuple(
                    atom.atom_id
                    for atom in self.repository.get_atoms_for_document(
                        exact_documents[0].document_id
                    )
                )
                exact_duplicates.append(record.record_id)
            elif exact_atoms:
                atom_ids = (exact_atoms[0].atom_id,)
                exact_duplicates.append(record.record_id)
            else:
                semantic_atom_id: str | None = None
                try:
                    semantic_atom_id = self._semantic_duplicate(namespace, record.content)
                except Exception as error:  # noqa: BLE001 - exact import can still proceed
                    calibration_warnings.append(
                        f"semantic_deduplication_unavailable:{type(error).__name__}"
                    )
                if semantic_atom_id:
                    atom_ids = (semantic_atom_id,)
                    semantic_duplicates.append(record.record_id)
                else:
                    result = IngestService(self.repository).ingest_text(
                        namespace=namespace,
                        source=f"mem0:{record.record_id}",
                        text=record.content,
                        explicit_tags=record.tags,
                        occurred_at=record.occurred_at,
                        atom_role=AtomRole.DERIVED,
                        payload_modality=PayloadModality.TEXT,
                        metadata={
                            **record.metadata,
                            "source_system": "mem0",
                            "mem0_record_id": record.record_id,
                            "lineage_relation": "derived_from_mem0",
                            "support_atom_ids": list(record.support_atom_ids),
                            "batch_atom_ids": list(record.batch_atom_ids),
                            "support_method": record.support_method,
                            "support_confidence": record.support_confidence,
                        },
                    )
                    atom_ids = result.atom_ids
                    if self.tag_proposer is not None:
                        TagEnrichmentService(
                            self.repository,
                            self.tag_proposer,
                            canonicalizer=(
                                SemanticTagCanonicalizer(self.embedder)
                                if self.embedder is not None
                                else None
                            ),
                        ).enrich_document(result.document_id)
                    try:
                        self._embed_new_atoms(atom_ids)
                    except Exception as error:  # noqa: BLE001 - calibration can degrade
                        calibration_warnings.append(
                            f"embedding_persistence_unavailable:{type(error).__name__}"
                        )
                    imported.append(record.record_id)

            record_atom_ids[record.record_id] = atom_ids
            calibration_count += self._persist_lineage(
                namespace=namespace,
                record_id=record.record_id,
                atom_ids=atom_ids,
            )
            created_links, created_signals = self._persist_source_lineage(
                namespace=namespace,
                record=record,
                atom_ids=atom_ids,
            )
            source_lineage_count += created_links
            calibration_count += created_signals
        conflict_count, conflict_signals = self._persist_conflicts(
            namespace=namespace,
            records=records,
            record_atom_ids=record_atom_ids,
        )
        return Mem0ImportResult(
            imported_record_ids=tuple(imported),
            exact_duplicate_record_ids=tuple(exact_duplicates),
            semantic_duplicate_record_ids=tuple(semantic_duplicates),
            record_atom_ids=record_atom_ids,
            conflict_links_created=conflict_count,
            source_lineage_links_created=source_lineage_count,
            calibration_signals_created=calibration_count + conflict_signals,
            calibration_warnings=tuple(dict.fromkeys(calibration_warnings)),
        )

    def _persist_source_lineage(
        self,
        *,
        namespace: str,
        record: Mem0Record,
        atom_ids: tuple[str, ...],
    ) -> tuple[int, int]:
        """Connect a distilled Mem0 memory to the native atoms that produced it."""

        requested_source_ids = self._validated_support_ids(
            namespace=namespace, record=record
        )
        if not requested_source_ids:
            return 0, 0

        proposed: dict[str, tuple[CalibrationSignal, AtomLink]] = {}
        support_confidence = (
            record.support_confidence
            if record.support_confidence is not None
            else 1.0
        )
        eligible_output_ids = self._mem0_output_atom_ids(atom_ids)
        for output_atom_id in eligible_output_ids:
            for source_atom_id in requested_source_ids:
                if output_atom_id == source_atom_id:
                    continue
                signal_id = stable_id(
                    "calibration",
                    namespace,
                    "mem0-fact-support-v2",
                    record.record_id,
                    output_atom_id,
                    source_atom_id,
                )
                signal = CalibrationSignal(
                    signal_id=signal_id,
                    namespace=namespace,
                    target_type=CalibrationTarget.ATOM_LINK,
                    target_id=output_atom_id,
                    related_id=source_atom_id,
                    relation_type=AtomLinkRelation.SUPPORTED_BY.value,
                    signal_type="mem0_fact_support",
                    value=1.0,
                    confidence=support_confidence,
                    multiplier=MEM0_LEARNING_MULTIPLIER,
                    provider="mem0",
                    profile_version="mem0-fact-support-v2",
                    source_reference=record.record_id,
                    metadata={
                        "support_method": record.support_method or "legacy_declared",
                        "batch_atom_ids": list(record.batch_atom_ids),
                    },
                )
                proposed[signal_id] = (
                    signal,
                    AtomLink(
                        from_atom_id=output_atom_id,
                        to_atom_id=source_atom_id,
                        relation=AtomLinkRelation.SUPPORTED_BY,
                        weight_raw=1.0,
                        confidence=support_confidence,
                        evidence_sources=(f"calibration:{signal_id}",),
                        metadata={
                            "source_system": "mem0",
                            "lineage": True,
                            "support_scope": "fact",
                            "support_method": record.support_method or "legacy_declared",
                            "batch_atom_ids": list(record.batch_atom_ids),
                        },
                    ),
                )
        existing = self.repository.get_calibration_signal_ids(tuple(proposed))
        pending = tuple(
            pair for signal_id, pair in proposed.items() if signal_id not in existing
        )
        if pending:
            self.repository.apply_calibration_updates(
                signals=tuple(signal for signal, _ in pending),
                atom_tags=(),
                atom_links=tuple(link for _, link in pending),
                tag_relations=(),
            )
        return len(pending), len(pending)

    def _validated_support_ids(
        self, *, namespace: str, record: Mem0Record
    ) -> tuple[str, ...]:
        raw_source_ids: object = record.support_atom_ids
        if not raw_source_ids:
            # Compatibility for trusted imports created before Mem0Record exposed
            # fact-level support as a first-class field.
            raw_source_ids = record.metadata.get("source_atom_ids", ())
        if not isinstance(raw_source_ids, list | tuple):
            return ()
        requested_source_ids = tuple(
            dict.fromkeys(str(value) for value in raw_source_ids if str(value).strip())
        )
        if not requested_source_ids:
            return ()
        sources = {
            atom.atom_id: atom for atom in self.repository.get_atoms(requested_source_ids)
        }
        invalid_ids = tuple(
            atom_id
            for atom_id in requested_source_ids
            if (
                atom_id not in sources
                or sources[atom_id].namespace != namespace
                or sources[atom_id].role is not AtomRole.SOURCE
            )
        )
        if invalid_ids:
            raise ValueError(
                "Mem0 support_atom_ids must reference source-role atoms in the import namespace: "
                + ", ".join(invalid_ids)
            )
        return requested_source_ids

    def _persist_lineage(
        self, *, namespace: str, record_id: str, atom_ids: tuple[str, ...]
    ) -> int:
        derived_atom_ids = self._mem0_output_atom_ids(atom_ids)
        signals = tuple(
            CalibrationSignal(
                signal_id=stable_id(
                    "calibration", namespace, "mem0-lineage-v1", record_id, atom_id
                ),
                namespace=namespace,
                target_type=CalibrationTarget.ATOM,
                target_id=atom_id,
                signal_type="derived_from_mem0",
                value=1.0,
                confidence=1.0,
                multiplier=1.0,
                provider="mem0",
                profile_version="mem0-lineage-v1",
                source_reference=record_id,
            )
            for atom_id in derived_atom_ids
        )
        existing = self.repository.get_calibration_signal_ids(
            tuple(signal.signal_id for signal in signals)
        )
        pending = tuple(signal for signal in signals if signal.signal_id not in existing)
        if pending:
            self.repository.apply_calibration_updates(
                signals=pending,
                atom_tags=(),
                atom_links=(),
                tag_relations=(),
            )
        return len(pending)

    def _mem0_output_atom_ids(self, atom_ids: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(
            atom.atom_id
            for atom in self.repository.get_atoms(atom_ids)
            if atom.role is AtomRole.DERIVED
            and atom.metadata.get("source_system") == "mem0"
        )

    def _semantic_duplicate(self, namespace: str, content: str) -> str | None:
        if self.embedder is None:
            return None
        vector = self.embedder.embed_query(content)
        hits = self.repository.search_semantic_hits(
            namespace=namespace,
            provider=self.embedder.provider,
            model=self.embedder.model,
            query_vector=vector,
            limit=1,
        )
        if hits and hits[0].score >= self.near_duplicate_threshold:
            return hits[0].atom_id
        return None

    def _embed_new_atoms(self, atom_ids: tuple[str, ...]) -> None:
        if self.embedder is None or not atom_ids:
            return
        atoms = self.repository.get_atoms(atom_ids)
        vectors = self.embedder.embed_documents(tuple(atom.content for atom in atoms))
        if len(vectors) != len(atoms):
            raise ValueError("embedder returned the wrong number of vectors")
        self.repository.upsert_embeddings(
            tuple(
                AtomEmbedding(
                    atom_id=atom.atom_id,
                    provider=self.embedder.provider,
                    model=self.embedder.model,
                    dimensions=len(vector),
                    vector=vector,
                    content_hash=atom.content_hash,
                )
                for atom, vector in zip(atoms, vectors, strict=True)
            )
        )

    def _persist_conflicts(
        self,
        *,
        namespace: str,
        records: tuple[Mem0Record, ...],
        record_atom_ids: dict[str, tuple[str, ...]],
    ) -> tuple[int, int]:
        signals: list[CalibrationSignal] = []
        links: list[AtomLink] = []
        for record in records:
            for conflicting_id in record.conflicts_with:
                if conflicting_id not in record_atom_ids:
                    continue
                for left in record_atom_ids[record.record_id]:
                    for right in record_atom_ids[conflicting_id]:
                        if left == right:
                            continue
                        source_id, target_id = sorted((left, right))
                        signal_id = stable_id(
                            "calibration",
                            namespace,
                            "mem0-conflict-v1",
                            source_id,
                            target_id,
                        )
                        signals.append(
                            CalibrationSignal(
                                signal_id=signal_id,
                                namespace=namespace,
                                target_type=CalibrationTarget.ATOM_LINK,
                                target_id=source_id,
                                related_id=target_id,
                                relation_type=AtomLinkRelation.CONFLICTS_WITH.value,
                                signal_type="mem0_declared_conflict",
                                value=1.0,
                                confidence=0.5,
                                multiplier=MEM0_LEARNING_MULTIPLIER,
                                provider="mem0",
                                profile_version="mem0-conflict-v1",
                                source_reference=record.record_id,
                            )
                        )
                        links.append(
                            AtomLink(
                                from_atom_id=source_id,
                                to_atom_id=target_id,
                                relation=AtomLinkRelation.CONFLICTS_WITH,
                                weight_raw=1.0,
                                confidence=0.5,
                                evidence_sources=(f"calibration:{signal_id}",),
                                metadata={"uncertainty": True, "source_system": "mem0"},
                            )
                        )
        unique_signals = {signal.signal_id: signal for signal in signals}
        existing = self.repository.get_calibration_signal_ids(tuple(unique_signals))
        new_signals = tuple(
            signal for signal_id, signal in unique_signals.items() if signal_id not in existing
        )
        if not new_signals:
            return 0, 0
        new_ids = {signal.signal_id for signal in new_signals}
        unique_links = {
            link.evidence_sources[0].removeprefix("calibration:"): link for link in links
        }
        new_links = tuple(
            link
            for signal_id, link in unique_links.items()
            if signal_id in new_ids
        )
        self.repository.apply_calibration_updates(
            signals=new_signals,
            atom_tags=(),
            atom_links=new_links,
            tag_relations=(),
        )
        return len(new_links), len(new_signals)


def load_mem0_records(path: Path) -> tuple[Mem0Record, ...]:
    """Load common Mem0 JSON array, object-wrapper, or JSONL export shapes."""

    text = path.read_text(encoding="utf-8")
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        payload = [json.loads(line) for line in text.splitlines() if line.strip()]
    if isinstance(payload, dict):
        for key in ("memories", "records", "results", "data"):
            if isinstance(payload.get(key), list):
                payload = payload[key]
                break
        else:
            payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("Mem0 export must contain a JSON array or JSONL records")
    return tuple(_record(item, index) for index, item in enumerate(payload))


def _record(raw: object, index: int) -> Mem0Record:
    if not isinstance(raw, dict):
        raise ValueError(f"Mem0 record {index} must be an object")
    content = next(
        (
            str(raw[key]).strip()
            for key in ("memory", "content", "text", "value")
            if raw.get(key) is not None and str(raw[key]).strip()
        ),
        "",
    )
    record_id = str(
        next(
            (raw[key] for key in ("id", "memory_id", "uuid") if raw.get(key) is not None),
            stable_id("mem0-record", content_hash(content), str(index)),
        )
    )
    metadata = dict(raw.get("metadata") or {}) if isinstance(raw.get("metadata"), dict) else {}
    raw_tags = raw.get("tags", metadata.get("tags", ()))
    tags = tuple(str(tag) for tag in raw_tags) if isinstance(raw_tags, list | tuple) else ()
    raw_conflicts = raw.get("conflicts_with", metadata.get("conflicts_with", ()))
    conflicts = (
        tuple(str(record_id) for record_id in raw_conflicts)
        if isinstance(raw_conflicts, list | tuple)
        else ()
    )
    raw_support = raw.get(
        "support_atom_ids",
        metadata.get("support_atom_ids", metadata.get("source_atom_ids", ())),
    )
    support_atom_ids = (
        tuple(dict.fromkeys(str(atom_id) for atom_id in raw_support))
        if isinstance(raw_support, list | tuple)
        else ()
    )
    raw_batch = raw.get("batch_atom_ids", metadata.get("batch_atom_ids", ()))
    batch_atom_ids = (
        tuple(dict.fromkeys(str(atom_id) for atom_id in raw_batch))
        if isinstance(raw_batch, list | tuple)
        else ()
    )
    raw_confidence = raw.get("support_confidence", metadata.get("support_confidence"))
    support_confidence = float(raw_confidence) if raw_confidence is not None else None
    support_method_value = raw.get("support_method", metadata.get("support_method"))
    support_method = str(support_method_value) if support_method_value is not None else None
    occurred_at = _optional_datetime(
        raw.get("updated_at") or raw.get("created_at") or raw.get("timestamp")
    )
    reserved = {
        "id",
        "memory_id",
        "uuid",
        "memory",
        "content",
        "text",
        "value",
        "tags",
        "conflicts_with",
        "support_atom_ids",
        "source_atom_ids",
        "batch_atom_ids",
        "support_method",
        "support_confidence",
        "metadata",
        "updated_at",
        "created_at",
        "timestamp",
    }
    metadata.update({str(key): value for key, value in raw.items() if key not in reserved})
    return Mem0Record(
        record_id=record_id,
        content=content,
        tags=tags,
        occurred_at=occurred_at,
        conflicts_with=conflicts,
        support_atom_ids=support_atom_ids,
        batch_atom_ids=batch_atom_ids,
        support_method=support_method,
        support_confidence=support_confidence,
        metadata=metadata,
    )


def _optional_datetime(value: object) -> datetime | None:
    if value is None or not str(value).strip():
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Mem0 timestamps must include a timezone")
    return parsed

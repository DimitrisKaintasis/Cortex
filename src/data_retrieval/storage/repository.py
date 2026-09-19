from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from data_retrieval.connectors.contracts import (
    ContextPack,
    Outcome,
    Record,
    RecordRef,
    Relation,
    Source,
    SourceRef,
    SyncBatch,
    SyncBatchAcknowledgement,
    SyncCommitAcknowledgement,
    SyncRun,
    Tombstone,
)
from data_retrieval.connectors.projection import ConnectorRecordProjection
from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomRole,
    AtomTag,
    CalibrationSignal,
    CalibrationTarget,
    Document,
    IngestionBundle,
    Tag,
    TagCandidate,
    TagCandidateState,
    TagRelation,
    WeightEvent,
)
from data_retrieval.retrieval.models import AtomEmbedding, SearchHit


class Repository(Protocol):
    """Complete persistence boundary for the tag domain."""

    def get_document(self, document_id: str) -> Document | None: ...

    def list_namespaces(self, prefix: str | None = None) -> tuple[str, ...]: ...

    def get_documents(self, document_ids: tuple[str, ...]) -> tuple[Document, ...]: ...

    def find_documents_by_content_hash(
        self, *, namespace: str, content_hash: str
    ) -> tuple[Document, ...]: ...

    def iter_document_ids(
        self, *, namespace: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]: ...

    def iter_document_ids_chronological(
        self, *, namespace: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]: ...

    def get_atoms_for_document(self, document_id: str) -> tuple[Atom, ...]: ...

    def iter_atom_ids_for_document(
        self, *, document_id: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]: ...

    def get_document_atom_count(self, document_id: str) -> int: ...

    def get_atom(self, atom_id: str) -> Atom | None: ...

    def get_atoms(self, atom_ids: tuple[str, ...]) -> tuple[Atom, ...]: ...

    def find_atoms_by_content_hash(
        self, *, namespace: str, content_hash: str
    ) -> tuple[Atom, ...]: ...

    def get_atom_links(self, atom_id: str) -> tuple[AtomLink, ...]: ...

    def list_atom_links(
        self,
        *,
        namespace: str,
        relation: str | None = None,
    ) -> tuple[AtomLink, ...]: ...

    def get_atom_links_touching(
        self,
        *,
        atom_ids: tuple[str, ...],
        relation: str | None = None,
    ) -> tuple[AtomLink, ...]: ...

    def atom_tags_for(self, atom_id: str) -> tuple[AtomTag, ...]: ...

    def list_atom_tags(self, namespace: str) -> tuple[AtomTag, ...]: ...

    def get_atom_tags_for_atoms(self, atom_ids: tuple[str, ...]) -> tuple[AtomTag, ...]: ...

    def list_atoms(
        self,
        *,
        namespace: str,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        kind: AtomKind | None = None,
        role: AtomRole | None = None,
    ) -> tuple[Atom, ...]: ...

    def iter_atoms(
        self,
        *,
        namespace: str,
        batch_size: int = 1_000,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        kind: AtomKind | None = None,
        role: AtomRole | None = None,
    ) -> Iterator[tuple[Atom, ...]]: ...

    def list_tags(self, namespace: str, limit: int | None = None) -> tuple[Tag, ...]: ...

    def get_tags(self, tag_ids: tuple[str, ...]) -> tuple[Tag, ...]: ...

    def get_tags_by_canonical(
        self, *, namespace: str, canonical_texts: tuple[str, ...]
    ) -> tuple[Tag, ...]: ...

    def get_tag_candidate(self, candidate_id: str) -> TagCandidate | None: ...

    def list_tag_candidates(
        self,
        *,
        namespace: str,
        state: TagCandidateState | None = None,
        limit: int | None = None,
    ) -> tuple[TagCandidate, ...]: ...

    def apply_tag_candidate_resolution(
        self,
        *,
        candidate: TagCandidate,
        tag: Tag | None,
        atom_tag: AtomTag | None,
    ) -> None:
        """Atomically resolve one candidate and optionally activate a canonical edge."""
        ...

    def list_tag_relations(
        self, *, namespace: str, relation_type: str | None = None
    ) -> tuple[TagRelation, ...]: ...

    def get_tag_relations_touching(
        self,
        *,
        tag_ids: tuple[str, ...],
        relation_type: str | None = None,
    ) -> tuple[TagRelation, ...]: ...

    def search_tag_hits(
        self,
        *,
        namespace: str,
        canonical_tags: tuple[str, ...],
        limit: int,
    ) -> tuple[SearchHit, ...]: ...

    def search_lexical_hits(
        self, *, namespace: str, query: str, limit: int
    ) -> tuple[SearchHit, ...]: ...

    def search_semantic_hits(
        self,
        *,
        namespace: str,
        provider: str,
        model: str,
        query_vector: tuple[float, ...],
        limit: int,
    ) -> tuple[SearchHit, ...]: ...

    def upsert_embeddings(self, embeddings: tuple[AtomEmbedding, ...]) -> None: ...

    def get_embeddings(
        self,
        *,
        atom_ids: tuple[str, ...],
        provider: str,
        model: str,
    ) -> dict[str, AtomEmbedding]: ...

    def record_retrieval_event(self, event: dict[str, object]) -> None: ...

    def get_retrieval_event(self, retrieval_id: str) -> dict[str, object] | None: ...

    def get_feedback_event(self, feedback_id: str) -> dict[str, object] | None: ...

    def get_calibration_signal_ids(self, signal_ids: tuple[str, ...]) -> frozenset[str]: ...

    def list_weight_events(
        self,
        *,
        namespace: str,
        target_type: CalibrationTarget | None = None,
        target_id: str | None = None,
        related_id: str | None = None,
        relation_type: str | None = None,
    ) -> tuple[WeightEvent, ...]: ...

    def apply_calibration_updates(
        self,
        *,
        signals: tuple[CalibrationSignal, ...],
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None: ...

    def apply_learning_updates(
        self,
        *,
        feedback_event: dict[str, object],
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None: ...

    def restore_weight_aggregates(
        self,
        *,
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None:
        """Repair serving aggregates from the immutable ledger without adding events."""
        ...

    def persist_ingestion(self, bundle: IngestionBundle) -> None:
        """Persist a complete ingestion bundle atomically."""
        ...

    def get_suppressed_connector_atom_ids(
        self, atom_ids: tuple[str, ...]
    ) -> frozenset[str]:
        """Return connector-projected atoms excluded from normal serving."""
        ...


@runtime_checkable
class ConnectorLifecycleRepository(Protocol):
    """Persistence boundary for replay-safe external connector state."""

    def register_connector_source(self, source: Source) -> Source: ...

    def get_connector_source(self, source: SourceRef) -> Source | None: ...

    def apply_connector_sync_batch(
        self,
        *,
        batch: SyncBatch,
        fingerprint: str,
        acknowledged_at: datetime,
    ) -> SyncBatchAcknowledgement: ...

    def commit_connector_sync(
        self,
        *,
        request_id: str,
        run_request_id: str,
        committed_at: datetime,
    ) -> SyncCommitAcknowledgement: ...

    def get_connector_sync_run(self, request_id: str) -> SyncRun | None: ...

    def get_connector_record(self, record: RecordRef) -> Record | None: ...

    def get_current_connector_record(
        self, *, source: SourceRef, external_id: str
    ) -> Record | None: ...

    def get_connector_record_predecessor(self, record: RecordRef) -> RecordRef | None: ...

    def get_connector_relation(
        self, *, source: SourceRef, relation_id: str, relation_version: str
    ) -> Relation | None: ...

    def get_connector_cursor(self, source: SourceRef) -> str | None: ...


@runtime_checkable
class ConnectorProjectionRepository(Protocol):
    """Persistence boundary for Cortex-owned serving projections."""

    def store_connector_record_projection(
        self, projection: ConnectorRecordProjection
    ) -> ConnectorRecordProjection: ...

    def get_connector_record_projection(
        self, record: RecordRef
    ) -> ConnectorRecordProjection | None: ...

    def apply_connector_tombstone_projection(
        self, *, tombstone: Tombstone, applied_at: datetime
    ) -> None: ...

    def get_suppressed_connector_atom_ids(
        self, atom_ids: tuple[str, ...]
    ) -> frozenset[str]: ...

    def connector_run_projection_complete(self, run_request_id: str) -> bool: ...

    def get_connector_projection_by_atom(
        self, atom_id: str
    ) -> ConnectorRecordProjection | None: ...

    def get_connector_atom_id(self, evidence_id: str) -> str | None: ...

    def get_connector_query_receipt(
        self, request_id: str
    ) -> tuple[str, ContextPack] | None: ...

    def get_connector_context(self, retrieval_id: str) -> ContextPack | None: ...

    def store_connector_query_receipt(
        self, *, fingerprint: str, context: ContextPack
    ) -> ContextPack: ...

    def get_connector_outcome_receipt(
        self, request_id: str
    ) -> tuple[str, Outcome] | None: ...

    def store_connector_outcome_receipt(
        self, *, fingerprint: str, outcome: Outcome
    ) -> Outcome: ...


class CortexRepository(
    Repository, ConnectorLifecycleRepository, ConnectorProjectionRepository, Protocol
):
    """Combined local application boundary implemented by every storage adapter."""


@runtime_checkable
class StagedIngestionRepository(Protocol):
    """Optional capability for bounded-memory, restart-safe ingestion."""

    def begin_staged_ingestion(
        self, *, document: Document, tags: tuple[Tag, ...]
    ) -> bool:
        """Create or resume a hidden staging document; false means already complete."""
        ...

    def append_staged_ingestion(
        self, *, atoms: tuple[Atom, ...], atom_tags: tuple[AtomTag, ...]
    ) -> None: ...

    def complete_staged_ingestion(self, *, document_id: str, atom_count: int) -> None: ...

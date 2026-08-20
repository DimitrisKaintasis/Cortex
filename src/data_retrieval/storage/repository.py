from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from typing import Protocol, runtime_checkable

from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomTag,
    CalibrationSignal,
    Document,
    IngestionBundle,
    Tag,
    TagRelation,
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
    ) -> tuple[Atom, ...]: ...

    def iter_atoms(
        self,
        *,
        namespace: str,
        batch_size: int = 1_000,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        kind: AtomKind | None = None,
    ) -> Iterator[tuple[Atom, ...]]: ...

    def list_tags(self, namespace: str, limit: int | None = None) -> tuple[Tag, ...]: ...

    def get_tags(self, tag_ids: tuple[str, ...]) -> tuple[Tag, ...]: ...

    def get_tags_by_canonical(
        self, *, namespace: str, canonical_texts: tuple[str, ...]
    ) -> tuple[Tag, ...]: ...

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

    def get_calibration_signal_ids(self, signal_ids: tuple[str, ...]) -> frozenset[str]: ...

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

    def persist_ingestion(self, bundle: IngestionBundle) -> None:
        """Persist a complete ingestion bundle atomically."""
        ...


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

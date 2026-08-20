from __future__ import annotations

import math
import re
from collections.abc import Iterator
from datetime import datetime
from threading import RLock

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
from data_retrieval.retrieval.embedding import cosine_similarity
from data_retrieval.retrieval.models import AtomEmbedding, SearchHit

TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", re.UNICODE)


class InMemoryRepository:
    """Thread-safe reference adapter used by domain tests and local experiments."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}
        self._atoms: dict[str, Atom] = {}
        self._atom_links: dict[tuple[str, str, str], AtomLink] = {}
        self._tags: dict[str, Tag] = {}
        self._atom_tags: dict[tuple[str, str], AtomTag] = {}
        self._embeddings: dict[tuple[str, str, str], AtomEmbedding] = {}
        self._tag_relations: dict[tuple[str, str, str], TagRelation] = {}
        self._retrieval_events: dict[str, dict[str, object]] = {}
        self._feedback_events: dict[str, dict[str, object]] = {}
        self._calibration_signals: dict[str, CalibrationSignal] = {}
        self._lock = RLock()

    def get_document(self, document_id: str) -> Document | None:
        with self._lock:
            return self._documents.get(document_id)

    def list_namespaces(self, prefix: str | None = None) -> tuple[str, ...]:
        with self._lock:
            return tuple(
                sorted(
                    {
                        document.namespace
                        for document in self._documents.values()
                        if prefix is None or document.namespace.startswith(prefix)
                    }
                )
            )

    def get_documents(self, document_ids: tuple[str, ...]) -> tuple[Document, ...]:
        with self._lock:
            return tuple(
                self._documents[document_id]
                for document_id in document_ids
                if document_id in self._documents
            )

    def find_documents_by_content_hash(
        self, *, namespace: str, content_hash: str
    ) -> tuple[Document, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        document
                        for document in self._documents.values()
                        if document.namespace == namespace
                        and document.content_hash == content_hash
                    ),
                    key=lambda document: document.document_id,
                )
            )

    def iter_document_ids(
        self, *, namespace: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        with self._lock:
            document_ids = tuple(
                sorted(
                    document.document_id
                    for document in self._documents.values()
                    if document.namespace == namespace
                )
            )
        for offset in range(0, len(document_ids), batch_size):
            yield document_ids[offset : offset + batch_size]

    def iter_document_ids_chronological(
        self, *, namespace: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        with self._lock:
            ordered = []
            for document in self._documents.values():
                if document.namespace != namespace:
                    continue
                occurred = tuple(
                    atom.occurred_at
                    for atom in self._atoms.values()
                    if atom.document_id == document.document_id
                    and atom.occurred_at is not None
                )
                ordered.append(
                    (min(occurred) if occurred else document.created_at, document.document_id)
                )
            document_ids = tuple(document_id for _, document_id in sorted(ordered))
        for offset in range(0, len(document_ids), batch_size):
            yield document_ids[offset : offset + batch_size]

    def get_atoms_for_document(self, document_id: str) -> tuple[Atom, ...]:
        with self._lock:
            atoms = (atom for atom in self._atoms.values() if atom.document_id == document_id)
            return tuple(sorted(atoms, key=lambda atom: atom.position))

    def iter_atom_ids_for_document(
        self, *, document_id: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        atom_ids = tuple(atom.atom_id for atom in self.get_atoms_for_document(document_id))
        for offset in range(0, len(atom_ids), batch_size):
            yield atom_ids[offset : offset + batch_size]

    def get_document_atom_count(self, document_id: str) -> int:
        with self._lock:
            return sum(atom.document_id == document_id for atom in self._atoms.values())

    def get_atom(self, atom_id: str) -> Atom | None:
        with self._lock:
            return self._atoms.get(atom_id)

    def get_atoms(self, atom_ids: tuple[str, ...]) -> tuple[Atom, ...]:
        with self._lock:
            return tuple(self._atoms[atom_id] for atom_id in atom_ids if atom_id in self._atoms)

    def find_atoms_by_content_hash(
        self, *, namespace: str, content_hash: str
    ) -> tuple[Atom, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        atom
                        for atom in self._atoms.values()
                        if atom.namespace == namespace and atom.content_hash == content_hash
                    ),
                    key=lambda atom: (atom.document_id, atom.position),
                )
            )

    def get_atom_links(self, atom_id: str) -> tuple[AtomLink, ...]:
        with self._lock:
            links = (link for link in self._atom_links.values() if link.from_atom_id == atom_id)
            return tuple(sorted(links, key=lambda link: (link.relation, link.to_atom_id)))

    def list_atom_links(
        self,
        *,
        namespace: str,
        relation: str | None = None,
    ) -> tuple[AtomLink, ...]:
        with self._lock:
            links = (
                link
                for link in self._atom_links.values()
                if self._atoms.get(link.from_atom_id) is not None
                and self._atoms[link.from_atom_id].namespace == namespace
                and (relation is None or link.relation == relation)
            )
            return tuple(
                sorted(
                    links,
                    key=lambda link: (link.relation, link.from_atom_id, link.to_atom_id),
                )
            )

    def get_atom_links_touching(
        self,
        *,
        atom_ids: tuple[str, ...],
        relation: str | None = None,
    ) -> tuple[AtomLink, ...]:
        selected = set(atom_ids)
        with self._lock:
            links = (
                link
                for link in self._atom_links.values()
                if (link.from_atom_id in selected or link.to_atom_id in selected)
                and (relation is None or link.relation == relation)
            )
            return tuple(
                sorted(
                    links,
                    key=lambda link: (link.relation, link.from_atom_id, link.to_atom_id),
                )
            )

    def list_tags(self, namespace: str, limit: int | None = None) -> tuple[Tag, ...]:
        with self._lock:
            tags = (tag for tag in self._tags.values() if tag.namespace == namespace)
            ordered = sorted(tags, key=lambda tag: tag.canonical_text)
            return tuple(ordered if limit is None else ordered[:limit])

    def get_tags(self, tag_ids: tuple[str, ...]) -> tuple[Tag, ...]:
        with self._lock:
            return tuple(self._tags[tag_id] for tag_id in tag_ids if tag_id in self._tags)

    def get_tags_by_canonical(
        self, *, namespace: str, canonical_texts: tuple[str, ...]
    ) -> tuple[Tag, ...]:
        selected = set(canonical_texts)
        with self._lock:
            return tuple(
                sorted(
                    (
                        tag
                        for tag in self._tags.values()
                        if tag.namespace == namespace and tag.canonical_text in selected
                    ),
                    key=lambda tag: tag.canonical_text,
                )
            )

    def list_tag_relations(
        self, *, namespace: str, relation_type: str | None = None
    ) -> tuple[TagRelation, ...]:
        with self._lock:
            namespace_tag_ids = {
                tag.tag_id for tag in self._tags.values() if tag.namespace == namespace
            }
            relations = (
                relation
                for relation in self._tag_relations.values()
                if relation.source_tag_id in namespace_tag_ids
                and (relation_type is None or relation.relation_type == relation_type)
            )
            return tuple(
                sorted(
                    relations,
                    key=lambda item: (
                        item.relation_type,
                        item.source_tag_id,
                        item.target_tag_id,
                    ),
                )
            )

    def get_tag_relations_touching(
        self,
        *,
        tag_ids: tuple[str, ...],
        relation_type: str | None = None,
    ) -> tuple[TagRelation, ...]:
        selected = set(tag_ids)
        with self._lock:
            relations = (
                relation
                for relation in self._tag_relations.values()
                if (
                    relation.source_tag_id in selected or relation.target_tag_id in selected
                )
                and (relation_type is None or relation.relation_type == relation_type)
            )
            return tuple(
                sorted(
                    relations,
                    key=lambda item: (
                        item.relation_type,
                        item.source_tag_id,
                        item.target_tag_id,
                    ),
                )
            )

    def list_atoms(
        self,
        *,
        namespace: str,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        kind: AtomKind | None = None,
    ) -> tuple[Atom, ...]:
        with self._lock:
            atoms = (
                atom
                for atom in self._atoms.values()
                if atom.namespace == namespace
                and (kind is None or atom.kind is kind)
                and (
                    occurred_from is None
                    or (atom.occurred_at is not None and atom.occurred_at >= occurred_from)
                )
                and (
                    occurred_to is None
                    or (atom.occurred_at is not None and atom.occurred_at < occurred_to)
                )
            )
            return tuple(
                sorted(
                    atoms,
                    key=lambda atom: (
                        atom.occurred_at is None,
                        atom.occurred_at or atom.created_at,
                        atom.document_id,
                        atom.position,
                    ),
                )
            )

    def iter_atoms(
        self,
        *,
        namespace: str,
        batch_size: int = 1_000,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        kind: AtomKind | None = None,
    ) -> Iterator[tuple[Atom, ...]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        atoms = self.list_atoms(
            namespace=namespace,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            kind=kind,
        )
        for offset in range(0, len(atoms), batch_size):
            yield atoms[offset : offset + batch_size]

    def search_tag_hits(
        self,
        *,
        namespace: str,
        canonical_tags: tuple[str, ...],
        limit: int,
    ) -> tuple[SearchHit, ...]:
        query_tags = set(canonical_tags)
        if not query_tags or limit <= 0:
            return ()
        tags = {
            tag.tag_id: tag
            for tag in self.list_tags(namespace)
            if tag.canonical_text in query_tags
        }
        scores: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        for edge in self.list_atom_tags(namespace):
            tag = tags.get(edge.tag_id)
            if tag is None:
                continue
            scores[edge.atom_id] = scores.get(edge.atom_id, 0.0) + (
                edge.confidence * math.log1p(edge.weight_raw) / math.log(2.0)
            ) / len(query_tags)
            evidence.setdefault(edge.atom_id, []).append(f"tag={tag.canonical_text}")
        ordered = sorted(scores, key=lambda atom_id: (scores[atom_id], atom_id), reverse=True)
        return tuple(
            SearchHit(atom_id, scores[atom_id], tuple(sorted(evidence[atom_id])))
            for atom_id in ordered[:limit]
        )

    def search_lexical_hits(
        self, *, namespace: str, query: str, limit: int
    ) -> tuple[SearchHit, ...]:
        query_terms = set(TOKEN_PATTERN.findall(query.casefold()))
        if not query_terms or limit <= 0:
            return ()
        hits: list[SearchHit] = []
        for atom in self.list_atoms(namespace=namespace):
            matches = sorted(
                query_terms.intersection(TOKEN_PATTERN.findall(atom.content.casefold()))
            )
            if matches:
                hits.append(
                    SearchHit(
                        atom.atom_id,
                        len(matches) / len(query_terms),
                        tuple(f"lexical={term}" for term in matches[:5]),
                    )
                )
        hits.sort(key=lambda hit: (hit.score, hit.atom_id), reverse=True)
        return tuple(hits[:limit])

    def search_semantic_hits(
        self,
        *,
        namespace: str,
        provider: str,
        model: str,
        query_vector: tuple[float, ...],
        limit: int,
    ) -> tuple[SearchHit, ...]:
        if not query_vector or limit <= 0:
            return ()
        atoms = self.list_atoms(namespace=namespace)
        embeddings = self.get_embeddings(
            atom_ids=tuple(atom.atom_id for atom in atoms), provider=provider, model=model
        )
        hits: list[SearchHit] = []
        for atom in atoms:
            embedding = embeddings.get(atom.atom_id)
            if embedding is None or embedding.content_hash != atom.content_hash:
                continue
            similarity = max(0.0, cosine_similarity(query_vector, embedding.vector))
            if similarity > 0.0:
                hits.append(
                    SearchHit(atom.atom_id, similarity, (f"semantic={similarity:.4f}",))
                )
        hits.sort(key=lambda hit: (hit.score, hit.atom_id), reverse=True)
        return tuple(hits[:limit])

    def upsert_embeddings(self, embeddings: tuple[AtomEmbedding, ...]) -> None:
        with self._lock:
            for embedding in embeddings:
                if embedding.atom_id not in self._atoms:
                    raise ValueError(f"unknown atom_id: {embedding.atom_id}")
            updated = dict(self._embeddings)
            updated.update(((item.atom_id, item.provider, item.model), item) for item in embeddings)
            self._embeddings = updated

    def get_embeddings(
        self,
        *,
        atom_ids: tuple[str, ...],
        provider: str,
        model: str,
    ) -> dict[str, AtomEmbedding]:
        with self._lock:
            return {
                atom_id: self._embeddings[(atom_id, provider, model)]
                for atom_id in atom_ids
                if (atom_id, provider, model) in self._embeddings
            }

    def record_retrieval_event(self, event: dict[str, object]) -> None:
        retrieval_id = str(event["retrieval_id"])
        with self._lock:
            self._retrieval_events[retrieval_id] = dict(event)

    def get_retrieval_event(self, retrieval_id: str) -> dict[str, object] | None:
        with self._lock:
            event = self._retrieval_events.get(retrieval_id)
            return dict(event) if event else None

    def get_calibration_signal_ids(self, signal_ids: tuple[str, ...]) -> frozenset[str]:
        with self._lock:
            return frozenset(
                signal_id
                for signal_id in signal_ids
                if signal_id in self._calibration_signals
            )

    def apply_calibration_updates(
        self,
        *,
        signals: tuple[CalibrationSignal, ...],
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None:
        """Atomically persist idempotent calibration evidence and absolute edge states."""
        with self._lock:
            duplicate_ids = {
                signal.signal_id
                for signal in signals
                if signal.signal_id in self._calibration_signals
            }
            if duplicate_ids:
                raise ValueError(f"calibration signal already exists: {sorted(duplicate_ids)[0]}")
            updated_signals = dict(self._calibration_signals)
            updated_signals.update((signal.signal_id, signal) for signal in signals)
            updated_atom_tags = dict(self._atom_tags)
            updated_atom_tags.update(((edge.atom_id, edge.tag_id), edge) for edge in atom_tags)
            updated_links = dict(self._atom_links)
            updated_links.update(
                ((edge.from_atom_id, edge.to_atom_id, edge.relation), edge) for edge in atom_links
            )
            updated_relations = dict(self._tag_relations)
            updated_relations.update(
                ((edge.source_tag_id, edge.target_tag_id, edge.relation_type), edge)
                for edge in tag_relations
            )
            self._calibration_signals = updated_signals
            self._atom_tags = updated_atom_tags
            self._atom_links = updated_links
            self._tag_relations = updated_relations

    def apply_learning_updates(
        self,
        *,
        feedback_event: dict[str, object],
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None:
        with self._lock:
            feedback_id = str(feedback_event["feedback_id"])
            if feedback_id in self._feedback_events:
                raise ValueError(f"feedback_id already exists: {feedback_id}")
            updated_atom_tags = dict(self._atom_tags)
            updated_links = dict(self._atom_links)
            updated_relations = dict(self._tag_relations)
            updated_feedback = dict(self._feedback_events)
            updated_atom_tags.update(((edge.atom_id, edge.tag_id), edge) for edge in atom_tags)
            updated_links.update(
                ((edge.from_atom_id, edge.to_atom_id, edge.relation), edge) for edge in atom_links
            )
            updated_relations.update(
                (
                    (edge.source_tag_id, edge.target_tag_id, edge.relation_type),
                    edge,
                )
                for edge in tag_relations
            )
            updated_feedback[feedback_id] = dict(feedback_event)
            self._atom_tags = updated_atom_tags
            self._atom_links = updated_links
            self._tag_relations = updated_relations
            self._feedback_events = updated_feedback

    def persist_ingestion(self, bundle: IngestionBundle) -> None:
        with self._lock:
            atom_ids = {atom.atom_id for atom in bundle.atoms}
            tag_ids = {tag.tag_id for tag in bundle.tags}
            known_atom_ids = atom_ids | set(self._atoms)
            for atom_tag in bundle.atom_tags:
                if atom_tag.atom_id not in atom_ids and atom_tag.atom_id not in self._atoms:
                    raise ValueError(f"unknown atom_id: {atom_tag.atom_id}")
                if atom_tag.tag_id not in tag_ids and atom_tag.tag_id not in self._tags:
                    raise ValueError(f"unknown tag_id: {atom_tag.tag_id}")
            for atom_link in bundle.atom_links:
                if atom_link.from_atom_id not in known_atom_ids:
                    raise ValueError(f"unknown from_atom_id: {atom_link.from_atom_id}")
                if atom_link.to_atom_id not in known_atom_ids:
                    raise ValueError(f"unknown to_atom_id: {atom_link.to_atom_id}")

            documents = dict(self._documents)
            atoms = dict(self._atoms)
            atom_links = dict(self._atom_links)
            tags = dict(self._tags)
            atom_tags = dict(self._atom_tags)

            documents[bundle.document.document_id] = bundle.document
            atoms.update((atom.atom_id, atom) for atom in bundle.atoms)
            tags.update((tag.tag_id, tag) for tag in bundle.tags)
            atom_tags.update(((edge.atom_id, edge.tag_id), edge) for edge in bundle.atom_tags)
            atom_links.update(
                ((edge.from_atom_id, edge.to_atom_id, edge.relation), edge)
                for edge in bundle.atom_links
            )

            self._documents = documents
            self._atoms = atoms
            self._atom_links = atom_links
            self._tags = tags
            self._atom_tags = atom_tags

    @property
    def document_count(self) -> int:
        with self._lock:
            return len(self._documents)

    @property
    def atom_count(self) -> int:
        with self._lock:
            return len(self._atoms)

    @property
    def tag_count(self) -> int:
        with self._lock:
            return len(self._tags)

    @property
    def atom_tag_count(self) -> int:
        with self._lock:
            return len(self._atom_tags)

    @property
    def atom_link_count(self) -> int:
        with self._lock:
            return len(self._atom_links)

    def atom_tags_for(self, atom_id: str) -> tuple[AtomTag, ...]:
        with self._lock:
            edges = (edge for edge in self._atom_tags.values() if edge.atom_id == atom_id)
            return tuple(sorted(edges, key=lambda edge: edge.tag_id))

    def list_atom_tags(self, namespace: str) -> tuple[AtomTag, ...]:
        with self._lock:
            atom_ids = {
                atom.atom_id for atom in self._atoms.values() if atom.namespace == namespace
            }
            edges = (edge for edge in self._atom_tags.values() if edge.atom_id in atom_ids)
            return tuple(sorted(edges, key=lambda edge: (edge.atom_id, edge.tag_id)))

    def get_atom_tags_for_atoms(self, atom_ids: tuple[str, ...]) -> tuple[AtomTag, ...]:
        selected = set(atom_ids)
        with self._lock:
            edges = (edge for edge in self._atom_tags.values() if edge.atom_id in selected)
            return tuple(sorted(edges, key=lambda edge: (edge.atom_id, edge.tag_id)))

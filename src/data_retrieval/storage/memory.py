from __future__ import annotations

import math
import re
from collections.abc import Iterator
from datetime import datetime
from threading import RLock

from data_retrieval.connectors.contracts import (
    Record,
    RecordRef,
    Relation,
    Source,
    SourceRef,
    SyncBatch,
    SyncBatchAcknowledgement,
    SyncCommitAcknowledgement,
    SyncItemFailure,
    SyncItemType,
    SyncMode,
    SyncRun,
    Tombstone,
)
from data_retrieval.connectors.projection import (
    ConnectorRecordProjection,
    ConnectorServingState,
)
from data_retrieval.core.weight_events import (
    calibration_transition_events,
    edge_coordinates,
    transition_events,
)
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
    TagState,
    WeightEvent,
    WeightEventSource,
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
        self._tag_candidates: dict[str, TagCandidate] = {}
        self._atom_tags: dict[tuple[str, str], AtomTag] = {}
        self._embeddings: dict[tuple[str, str, str], AtomEmbedding] = {}
        self._tag_relations: dict[tuple[str, str, str], TagRelation] = {}
        self._retrieval_events: dict[str, dict[str, object]] = {}
        self._feedback_events: dict[str, dict[str, object]] = {}
        self._calibration_signals: dict[str, CalibrationSignal] = {}
        self._weight_events: dict[str, WeightEvent] = {}
        self._connector_sources: dict[tuple[str, str], Source] = {}
        self._connector_runs: dict[str, SyncRun] = {}
        self._connector_batches: dict[
            tuple[str, str, int], tuple[str, SyncBatchAcknowledgement, SyncBatch]
        ] = {}
        self._connector_records: dict[tuple[str, str, str, str], Record] = {}
        self._connector_record_predecessors: dict[
            tuple[str, str, str, str], RecordRef | None
        ] = {}
        self._connector_current_records: dict[tuple[str, str, str], str] = {}
        self._connector_relations: dict[tuple[str, str, str, str], Relation] = {}
        self._connector_tombstones: dict[tuple[str, str, str, str], Tombstone] = {}
        self._connector_current_tombstones: dict[tuple[str, str, str], Tombstone] = {}
        self._connector_cursors: dict[tuple[str, str], str] = {}
        self._connector_commits: dict[str, SyncCommitAcknowledgement] = {}
        self._connector_committed_runs: dict[str, str] = {}
        self._connector_record_projections: dict[
            tuple[str, str, str, str], ConnectorRecordProjection
        ] = {}
        self._connector_tombstone_projections: set[tuple[str, str, str, str]] = set()
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
            tags = (
                tag
                for tag in self._tags.values()
                if tag.namespace == namespace and tag.state is TagState.CANONICAL
            )
            ordered = sorted(tags, key=lambda tag: tag.canonical_text)
            return tuple(ordered if limit is None else ordered[:limit])

    def get_tags(self, tag_ids: tuple[str, ...]) -> tuple[Tag, ...]:
        with self._lock:
            return tuple(
                self._tags[tag_id]
                for tag_id in tag_ids
                if tag_id in self._tags and self._tags[tag_id].state is TagState.CANONICAL
            )

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
                        if tag.namespace == namespace
                        and tag.state is TagState.CANONICAL
                        and tag.canonical_text in selected
                    ),
                    key=lambda tag: tag.canonical_text,
                )
            )

    def get_tag_candidate(self, candidate_id: str) -> TagCandidate | None:
        with self._lock:
            return self._tag_candidates.get(candidate_id)

    def list_tag_candidates(
        self,
        *,
        namespace: str,
        state: TagCandidateState | None = None,
        limit: int | None = None,
    ) -> tuple[TagCandidate, ...]:
        if limit is not None and limit <= 0:
            return ()
        with self._lock:
            candidates = sorted(
                (
                    candidate
                    for candidate in self._tag_candidates.values()
                    if candidate.namespace == namespace
                    and (state is None or candidate.state is state)
                ),
                key=lambda candidate: (candidate.created_at, candidate.candidate_id),
            )
            return tuple(candidates if limit is None else candidates[:limit])

    def apply_tag_candidate_resolution(
        self,
        *,
        candidate: TagCandidate,
        tag: Tag | None,
        atom_tag: AtomTag | None,
    ) -> None:
        with self._lock:
            existing = self._tag_candidates.get(candidate.candidate_id)
            if existing is None:
                raise ValueError(f"unknown candidate_id: {candidate.candidate_id}")
            if existing.state is not TagCandidateState.PROPOSED:
                raise ValueError(f"candidate is already resolved: {candidate.candidate_id}")
            if candidate.atom_id not in self._atoms:
                raise ValueError(f"unknown atom_id: {candidate.atom_id}")
            if tag is not None and tag.state is not TagState.CANONICAL:
                raise ValueError("resolved tag must be canonical")
            if (tag is None) != (atom_tag is None):
                raise ValueError("tag and atom_tag must be supplied together")
            tags = dict(self._tags)
            atom_tags = dict(self._atom_tags)
            candidates = dict(self._tag_candidates)
            weight_events = dict(self._weight_events)
            if tag is not None and atom_tag is not None:
                weight_events = self._with_weight_transitions(
                    edges=(atom_tag,),
                    namespace=candidate.namespace,
                    source_type=WeightEventSource.TAG_REVIEW,
                    source_id=candidate.candidate_id,
                    policy_version="tag-review-v1",
                )
                tags[tag.tag_id] = tag
                atom_tags[(atom_tag.atom_id, atom_tag.tag_id)] = atom_tag
            candidates[candidate.candidate_id] = candidate
            self._tags = tags
            self._atom_tags = atom_tags
            self._tag_candidates = candidates
            self._weight_events = weight_events

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
        role: AtomRole | None = None,
    ) -> tuple[Atom, ...]:
        with self._lock:
            atoms = (
                atom
                for atom in self._atoms.values()
                if atom.namespace == namespace
                and (kind is None or atom.kind is kind)
                and (role is None or atom.role is role)
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
        role: AtomRole | None = None,
    ) -> Iterator[tuple[Atom, ...]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        atoms = self.list_atoms(
            namespace=namespace,
            occurred_from=occurred_from,
            occurred_to=occurred_to,
            kind=kind,
            role=role,
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

    def list_weight_events(
        self,
        *,
        namespace: str,
        target_type: CalibrationTarget | None = None,
        target_id: str | None = None,
        related_id: str | None = None,
        relation_type: str | None = None,
    ) -> tuple[WeightEvent, ...]:
        with self._lock:
            return tuple(
                sorted(
                    (
                        event
                        for event in self._weight_events.values()
                        if event.namespace == namespace
                        and (target_type is None or event.target_type is target_type)
                        and (target_id is None or event.target_id == target_id)
                        and (related_id is None or event.related_id == related_id)
                        and (relation_type is None or event.relation_type == relation_type)
                    ),
                    key=lambda event: (event.created_at, event.event_id),
                )
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
            events = calibration_transition_events(
                namespace=signals[0].namespace if signals else "unknown",
                edges=(*atom_tags, *atom_links, *tag_relations),
                previous_weights=self._previous_weight_map(),
                signals=signals,
            )
            weight_events = self._with_events(events)
            self._calibration_signals = updated_signals
            self._atom_tags = updated_atom_tags
            self._atom_links = updated_links
            self._tag_relations = updated_relations
            self._weight_events = weight_events

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
            weight_events = self._with_weight_transitions(
                edges=(*atom_tags, *atom_links, *tag_relations),
                namespace=str(feedback_event["namespace"]),
                source_type=WeightEventSource.FEEDBACK,
                source_id=feedback_id,
                policy_version=str(
                    feedback_event.get("policy_version", "bounded-feedback-v1")
                ),
                metadata={
                    "retrieval_id": str(feedback_event["retrieval_id"]),
                    "outcome": str(feedback_event["outcome"]),
                },
            )
            self._atom_tags = updated_atom_tags
            self._atom_links = updated_links
            self._tag_relations = updated_relations
            self._feedback_events = updated_feedback
            self._weight_events = weight_events

    def restore_weight_aggregates(
        self,
        *,
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None:
        with self._lock:
            self._atom_tags.update(
                ((edge.atom_id, edge.tag_id), edge) for edge in atom_tags
            )
            self._atom_links.update(
                ((edge.from_atom_id, edge.to_atom_id, edge.relation), edge)
                for edge in atom_links
            )
            self._tag_relations.update(
                ((edge.source_tag_id, edge.target_tag_id, edge.relation_type), edge)
                for edge in tag_relations
            )

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
            tag_candidates = dict(self._tag_candidates)
            weight_events = self._with_weight_transitions(
                edges=(*bundle.atom_tags, *bundle.atom_links),
                namespace=bundle.document.namespace,
                source_type=WeightEventSource.INGESTION,
                source_id=bundle.document.document_id,
                policy_version="canonical-ingestion-v1",
            )

            documents[bundle.document.document_id] = bundle.document
            atoms.update((atom.atom_id, atom) for atom in bundle.atoms)
            tags.update((tag.tag_id, tag) for tag in bundle.tags)
            atom_tags.update(((edge.atom_id, edge.tag_id), edge) for edge in bundle.atom_tags)
            tag_candidates.update(
                (candidate.candidate_id, candidate) for candidate in bundle.tag_candidates
            )
            atom_links.update(
                ((edge.from_atom_id, edge.to_atom_id, edge.relation), edge)
                for edge in bundle.atom_links
            )

            self._documents = documents
            self._atoms = atoms
            self._atom_links = atom_links
            self._tags = tags
            self._atom_tags = atom_tags
            self._tag_candidates = tag_candidates
            self._weight_events = weight_events

    def register_connector_source(self, source: Source) -> Source:
        with self._lock:
            existing = self._connector_sources.get(source.source.key)
            if existing is not None and existing != source:
                raise ValueError("connector source registration conflicts with existing source")
            self._connector_sources[source.source.key] = source
            return source

    def get_connector_source(self, source: SourceRef) -> Source | None:
        with self._lock:
            return self._connector_sources.get(source.key)

    def apply_connector_sync_batch(
        self,
        *,
        batch: SyncBatch,
        fingerprint: str,
        acknowledged_at: datetime,
    ) -> SyncBatchAcknowledgement:
        with self._lock:
            if batch.run.source.key not in self._connector_sources:
                raise ValueError("connector source is not registered")
            existing_run = self._connector_runs.get(batch.run.request_id)
            if existing_run is not None and existing_run != batch.run:
                raise ValueError("sync request identity conflicts with existing run")
            if existing_run is None:
                current_cursor = self._connector_cursors.get(batch.run.source.key)
                if (
                    batch.run.mode is SyncMode.INCREMENTAL
                    and batch.run.previous_cursor != current_cursor
                ):
                    raise ValueError("sync previous_cursor does not match committed cursor")

            batch_key = (batch.run.request_id, batch.batch_id, batch.sequence)
            existing_batch = self._connector_batches.get(batch_key)
            if existing_batch is not None:
                existing_fingerprint, acknowledgement, _ = existing_batch
                if existing_fingerprint != fingerprint:
                    raise ValueError("sync batch identity conflicts with different payload")
                return acknowledgement

            records = dict(self._connector_records)
            predecessors = dict(self._connector_record_predecessors)
            current_records = dict(self._connector_current_records)
            relations = dict(self._connector_relations)
            tombstones = dict(self._connector_tombstones)
            current_tombstones = dict(self._connector_current_tombstones)
            failures: list[SyncItemFailure] = []
            accepted_records = 0
            accepted_relations = 0
            accepted_tombstones = 0

            for record in batch.records:
                key = record.ref.version_key
                existing = records.get(key)
                if existing is not None and existing != record:
                    failures.append(
                        SyncItemFailure(
                            item_type=SyncItemType.RECORD,
                            item_id=record.ref.external_id,
                            item_version=record.ref.external_version or "",
                            code="version_conflict",
                            message="record version already exists with different content",
                            retryable=False,
                        )
                    )
                    continue
                if existing is None:
                    object_key = record.ref.object_key
                    current_version = current_records.get(object_key)
                    current = (
                        records.get((*object_key, current_version))
                        if current_version is not None
                        else None
                    )
                    becomes_current = current is None or record.observed_at >= current.observed_at
                    predecessor = current.ref if becomes_current and current is not None else None
                    records[key] = record
                    predecessors[key] = predecessor
                    if becomes_current:
                        current_records[object_key] = record.ref.external_version or ""
                accepted_records += 1

            for relation in batch.relations:
                key = relation.version_key
                existing = relations.get(key)
                if existing is not None and existing != relation:
                    failures.append(
                        SyncItemFailure(
                            item_type=SyncItemType.RELATION,
                            item_id=relation.relation_id,
                            item_version=relation.relation_version,
                            code="version_conflict",
                            message="relation version already exists with different content",
                            retryable=False,
                        )
                    )
                    continue
                missing = [
                    endpoint
                    for endpoint in (relation.source, relation.target)
                    if endpoint.external_version is None
                    or endpoint.version_key not in records
                ]
                if missing:
                    failures.append(
                        SyncItemFailure(
                            item_type=SyncItemType.RELATION,
                            item_id=relation.relation_id,
                            item_version=relation.relation_version,
                            code="missing_endpoint",
                            message="relation endpoint record version is not durable",
                            retryable=True,
                        )
                    )
                    continue
                if existing is None:
                    relations[key] = relation
                accepted_relations += 1

            for tombstone in batch.tombstones:
                key = tombstone.version_key
                existing = tombstones.get(key)
                if existing is not None and existing != tombstone:
                    failures.append(
                        SyncItemFailure(
                            item_type=SyncItemType.TOMBSTONE,
                            item_id=tombstone.record.external_id,
                            item_version=tombstone.tombstone_version,
                            code="version_conflict",
                            message="tombstone version already exists with different content",
                            retryable=False,
                        )
                    )
                    continue
                if existing is None:
                    tombstones[key] = tombstone
                    current = current_tombstones.get(tombstone.record.object_key)
                    if current is None or tombstone.observed_at >= current.observed_at:
                        current_tombstones[tombstone.record.object_key] = tombstone
                accepted_tombstones += 1

            acknowledgement = SyncBatchAcknowledgement(
                run_request_id=batch.run.request_id,
                batch_id=batch.batch_id,
                sequence=batch.sequence,
                acknowledged_at=acknowledged_at,
                accepted_records=accepted_records,
                accepted_relations=accepted_relations,
                accepted_tombstones=accepted_tombstones,
                failures=tuple(failures),
            )
            self._connector_runs[batch.run.request_id] = batch.run
            self._connector_batches[batch_key] = (fingerprint, acknowledgement, batch)
            self._connector_records = records
            self._connector_record_predecessors = predecessors
            self._connector_current_records = current_records
            self._connector_relations = relations
            self._connector_tombstones = tombstones
            self._connector_current_tombstones = current_tombstones
            return acknowledgement

    def commit_connector_sync(
        self,
        *,
        request_id: str,
        run_request_id: str,
        committed_at: datetime,
    ) -> SyncCommitAcknowledgement:
        with self._lock:
            existing = self._connector_commits.get(request_id)
            if existing is not None:
                if existing.run_request_id != run_request_id:
                    raise ValueError("sync commit request identity conflicts with existing commit")
                return existing
            prior_request = self._connector_committed_runs.get(run_request_id)
            if prior_request is not None:
                raise ValueError("sync run was already committed by a different request")
            run = self._connector_runs.get(run_request_id)
            if run is None:
                raise ValueError("sync run does not exist")
            run_batches = [
                (acknowledgement, batch)
                for (stored_run, _, _), (
                    _,
                    acknowledgement,
                    batch,
                ) in self._connector_batches.items()
                if stored_run == run_request_id
            ]
            if not run_batches:
                raise ValueError("sync run has no durable batches")
            unresolved_failures = tuple(
                failure
                for acknowledgement, _ in run_batches
                for failure in acknowledgement.failures
                if not self._connector_failure_is_resolved(run.source, failure)
            )
            if unresolved_failures:
                raise ValueError("sync run has item failures and cannot commit its cursor")
            if not self.connector_run_projection_complete(run_request_id):
                raise ValueError(
                    "sync run has unprojected records or tombstones and cannot commit its cursor"
                )
            if run.proposed_cursor is None:
                raise ValueError("sync run requires proposed_cursor before commit")
            acknowledgement = SyncCommitAcknowledgement(
                request_id=request_id,
                run_request_id=run_request_id,
                source=run.source,
                committed_cursor=run.proposed_cursor,
                committed_at=committed_at,
            )
            self._connector_cursors[run.source.key] = run.proposed_cursor
            self._connector_commits[request_id] = acknowledgement
            self._connector_committed_runs[run_request_id] = request_id
            return acknowledgement

    def _connector_failure_is_resolved(
        self, source: SourceRef, failure: SyncItemFailure
    ) -> bool:
        return (
            failure.retryable
            and failure.item_type is SyncItemType.RELATION
            and (*source.key, failure.item_id, failure.item_version)
            in self._connector_relations
        )

    def get_connector_sync_run(self, request_id: str) -> SyncRun | None:
        with self._lock:
            return self._connector_runs.get(request_id)

    def get_connector_record(self, record: RecordRef) -> Record | None:
        if record.external_version is None:
            raise ValueError("connector record lookup requires external_version")
        with self._lock:
            return self._connector_records.get(record.version_key)

    def get_current_connector_record(
        self, *, source: SourceRef, external_id: str
    ) -> Record | None:
        object_key = (*source.key, external_id)
        with self._lock:
            version = self._connector_current_records.get(object_key)
            if version is None:
                return None
            record = self._connector_records[(*object_key, version)]
            tombstone = self._connector_current_tombstones.get(object_key)
            if tombstone is not None and tombstone.observed_at >= record.observed_at:
                return None
            return record

    def get_connector_record_predecessor(self, record: RecordRef) -> RecordRef | None:
        if record.external_version is None:
            raise ValueError("connector predecessor lookup requires external_version")
        with self._lock:
            return self._connector_record_predecessors.get(record.version_key)

    def get_connector_relation(
        self, *, source: SourceRef, relation_id: str, relation_version: str
    ) -> Relation | None:
        with self._lock:
            return self._connector_relations.get(
                (*source.key, relation_id, relation_version)
            )

    def get_connector_cursor(self, source: SourceRef) -> str | None:
        with self._lock:
            return self._connector_cursors.get(source.key)

    def store_connector_record_projection(
        self, projection: ConnectorRecordProjection
    ) -> ConnectorRecordProjection:
        with self._lock:
            existing = self._connector_record_projections.get(projection.record.version_key)
            if existing is not None:
                if (
                    existing.namespace != projection.namespace
                    or existing.document_id != projection.document_id
                    or existing.atom_ids != projection.atom_ids
                    or existing.evidence_ids != projection.evidence_ids
                ):
                    raise ValueError("connector record projection conflicts with existing data")
                return existing
            tombstone = self._connector_current_tombstones.get(projection.record.object_key)
            record = self._connector_records[projection.record.version_key]
            stored = projection
            if tombstone is not None and tombstone.observed_at >= record.observed_at:
                stored = ConnectorRecordProjection(
                    record=projection.record,
                    namespace=projection.namespace,
                    document_id=projection.document_id,
                    atom_ids=projection.atom_ids,
                    evidence_ids=projection.evidence_ids,
                    projected_at=projection.projected_at,
                    serving_state=ConnectorServingState.TOMBSTONED,
                )
            self._connector_record_projections[projection.record.version_key] = stored
            return stored

    def get_connector_record_projection(
        self, record: RecordRef
    ) -> ConnectorRecordProjection | None:
        if record.external_version is None:
            raise ValueError("connector projection lookup requires external_version")
        with self._lock:
            return self._connector_record_projections.get(record.version_key)

    def apply_connector_tombstone_projection(
        self, *, tombstone: Tombstone, applied_at: datetime
    ) -> None:
        with self._lock:
            self._connector_tombstone_projections.add(tombstone.version_key)
            for key, projection in tuple(self._connector_record_projections.items()):
                if projection.record.object_key != tombstone.record.object_key:
                    continue
                record = self._connector_records[key]
                if record.observed_at > tombstone.observed_at:
                    continue
                self._connector_record_projections[key] = ConnectorRecordProjection(
                    record=projection.record,
                    namespace=projection.namespace,
                    document_id=projection.document_id,
                    atom_ids=projection.atom_ids,
                    evidence_ids=projection.evidence_ids,
                    projected_at=projection.projected_at,
                    serving_state=ConnectorServingState.TOMBSTONED,
                )

    def get_suppressed_connector_atom_ids(
        self, atom_ids: tuple[str, ...]
    ) -> frozenset[str]:
        selected = set(atom_ids)
        with self._lock:
            return frozenset(
                atom_id
                for projection in self._connector_record_projections.values()
                if projection.serving_state is ConnectorServingState.TOMBSTONED
                for atom_id in projection.atom_ids
                if atom_id in selected
            )

    def connector_run_projection_complete(self, run_request_id: str) -> bool:
        with self._lock:
            batches = tuple(
                (acknowledgement, batch)
                for (stored_run, _, _), (
                    _,
                    acknowledgement,
                    batch,
                ) in self._connector_batches.items()
                if stored_run == run_request_id
            )
            for acknowledgement, batch in batches:
                failures = {failure.key for failure in acknowledgement.failures}
                for record in batch.records:
                    key = (
                        SyncItemType.RECORD,
                        record.ref.external_id,
                        record.ref.external_version or "",
                    )
                    if (
                        key not in failures
                        and record.ref.version_key
                        not in self._connector_record_projections
                    ):
                        return False
                for tombstone in batch.tombstones:
                    key = (
                        SyncItemType.TOMBSTONE,
                        tombstone.record.external_id,
                        tombstone.tombstone_version,
                    )
                    if (
                        key not in failures
                        and tombstone.version_key
                        not in self._connector_tombstone_projections
                    ):
                        return False
            return True

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

    def _with_weight_transitions(
        self,
        *,
        edges: tuple[AtomTag | AtomLink | TagRelation, ...],
        namespace: str,
        source_type: WeightEventSource,
        source_id: str,
        policy_version: str,
        metadata: dict[str, object] | None = None,
    ) -> dict[str, WeightEvent]:
        events = transition_events(
            namespace=namespace,
            edges=edges,
            previous_weights=self._previous_weight_map(),
            source_type=source_type,
            source_id=source_id,
            policy_version=policy_version,
            metadata=metadata,
        )
        return self._with_events(events)

    def _previous_weight_map(self):
        previous_edges = (
            *self._atom_tags.values(),
            *self._atom_links.values(),
            *self._tag_relations.values(),
        )
        return {
            edge_coordinates(edge): edge.weight_raw for edge in previous_edges
        }

    def _with_events(
        self, events: tuple[WeightEvent, ...]
    ) -> dict[str, WeightEvent]:
        updated = dict(self._weight_events)
        for event in events:
            existing = updated.get(event.event_id)
            if existing is not None and existing != event:
                raise ValueError(f"weight event collision: {event.event_id}")
            updated[event.event_id] = event
        return updated

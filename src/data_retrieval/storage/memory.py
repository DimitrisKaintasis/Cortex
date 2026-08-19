from __future__ import annotations

from datetime import datetime
from threading import RLock

from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomTag,
    Document,
    IngestionBundle,
    Tag,
)


class InMemoryRepository:
    """Thread-safe reference adapter used by domain tests and local experiments."""

    def __init__(self) -> None:
        self._documents: dict[str, Document] = {}
        self._atoms: dict[str, Atom] = {}
        self._atom_links: dict[tuple[str, str, str], AtomLink] = {}
        self._tags: dict[str, Tag] = {}
        self._atom_tags: dict[tuple[str, str], AtomTag] = {}
        self._lock = RLock()

    def get_document(self, document_id: str) -> Document | None:
        with self._lock:
            return self._documents.get(document_id)

    def get_atoms_for_document(self, document_id: str) -> tuple[Atom, ...]:
        with self._lock:
            atoms = (atom for atom in self._atoms.values() if atom.document_id == document_id)
            return tuple(sorted(atoms, key=lambda atom: atom.position))

    def get_atom(self, atom_id: str) -> Atom | None:
        with self._lock:
            return self._atoms.get(atom_id)

    def get_atom_links(self, atom_id: str) -> tuple[AtomLink, ...]:
        with self._lock:
            links = (link for link in self._atom_links.values() if link.from_atom_id == atom_id)
            return tuple(sorted(links, key=lambda link: (link.relation, link.to_atom_id)))

    def list_tags(self, namespace: str) -> tuple[Tag, ...]:
        with self._lock:
            tags = (tag for tag in self._tags.values() if tag.namespace == namespace)
            return tuple(sorted(tags, key=lambda tag: tag.canonical_text))

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
                and atom.occurred_at is not None
                and (occurred_from is None or atom.occurred_at >= occurred_from)
                and (occurred_to is None or atom.occurred_at < occurred_to)
            )
            return tuple(
                sorted(atoms, key=lambda atom: (atom.occurred_at, atom.document_id, atom.position))
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

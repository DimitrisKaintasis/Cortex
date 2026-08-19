from __future__ import annotations

from datetime import datetime
from typing import Protocol

from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomTag,
    Document,
    IngestionBundle,
    Tag,
)


class Repository(Protocol):
    """Complete persistence boundary for the tag domain."""

    def get_document(self, document_id: str) -> Document | None: ...

    def get_atoms_for_document(self, document_id: str) -> tuple[Atom, ...]: ...

    def get_atom(self, atom_id: str) -> Atom | None: ...

    def get_atom_links(self, atom_id: str) -> tuple[AtomLink, ...]: ...

    def atom_tags_for(self, atom_id: str) -> tuple[AtomTag, ...]: ...

    def list_atoms(
        self,
        *,
        namespace: str,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        kind: AtomKind | None = None,
    ) -> tuple[Atom, ...]: ...

    def list_tags(self, namespace: str) -> tuple[Tag, ...]: ...

    def persist_ingestion(self, bundle: IngestionBundle) -> None:
        """Persist a complete ingestion bundle atomically."""
        ...

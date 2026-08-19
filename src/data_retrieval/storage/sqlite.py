from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import Any

from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    AtomTag,
    Document,
    IngestionBundle,
    Tag,
    TagLevel,
    TagOrigin,
    TagState,
)


class SQLiteRepository:
    """Durable canonical storage for documents, atoms, tags, and their links."""

    def __init__(self, path: Path) -> None:
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = RLock()
        with self._connection:
            self._connection.execute("PRAGMA foreign_keys = ON")
            self._connection.execute("PRAGMA journal_mode = WAL")
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS documents (
                    document_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS atoms (
                    atom_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    namespace TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    occurred_at TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    UNIQUE(document_id, position)
                );

                CREATE TABLE IF NOT EXISTS tags (
                    tag_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    canonical_text TEXT NOT NULL,
                    display_text TEXT NOT NULL,
                    level TEXT NOT NULL,
                    state TEXT NOT NULL,
                    aliases_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(namespace, canonical_text)
                );

                CREATE TABLE IF NOT EXISTS atom_tags (
                    atom_id TEXT NOT NULL REFERENCES atoms(atom_id),
                    tag_id TEXT NOT NULL REFERENCES tags(tag_id),
                    weight_raw REAL NOT NULL CHECK(weight_raw >= 0),
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    origin TEXT NOT NULL,
                    evidence_sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(atom_id, tag_id)
                );

                CREATE TABLE IF NOT EXISTS atom_links (
                    from_atom_id TEXT NOT NULL REFERENCES atoms(atom_id),
                    to_atom_id TEXT NOT NULL REFERENCES atoms(atom_id),
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(from_atom_id, to_atom_id, relation)
                );

                CREATE INDEX IF NOT EXISTS idx_atoms_document_position
                    ON atoms(document_id, position);
                CREATE INDEX IF NOT EXISTS idx_atoms_namespace_occurred
                    ON atoms(namespace, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_tags_namespace_canonical
                    ON tags(namespace, canonical_text);
                CREATE INDEX IF NOT EXISTS idx_atom_links_to
                    ON atom_links(to_atom_id, relation);
                """
            )

    def get_document(self, document_id: str) -> Document | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM documents WHERE document_id = ?", (document_id,)
            ).fetchone()
        return self._document(row) if row else None

    def get_atoms_for_document(self, document_id: str) -> tuple[Atom, ...]:
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM atoms WHERE document_id = ? ORDER BY position",
                (document_id,),
            ).fetchall()
        return tuple(self._atom(row) for row in rows)

    def get_atom(self, atom_id: str) -> Atom | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM atoms WHERE atom_id = ?", (atom_id,)
            ).fetchone()
        return self._atom(row) if row else None

    def get_atom_links(self, atom_id: str) -> tuple[AtomLink, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM atom_links
                WHERE from_atom_id = ?
                ORDER BY relation, to_atom_id
                """,
                (atom_id,),
            ).fetchall()
        return tuple(self._atom_link(row) for row in rows)

    def list_tags(self, namespace: str) -> tuple[Tag, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM tags
                WHERE namespace = ?
                ORDER BY canonical_text
                """,
                (namespace,),
            ).fetchall()
        return tuple(self._tag(row) for row in rows)

    def list_atoms(
        self,
        *,
        namespace: str,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        kind: AtomKind | None = None,
    ) -> tuple[Atom, ...]:
        clauses = ["namespace = ?", "occurred_at IS NOT NULL"]
        parameters: list[Any] = [namespace]
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind.value)
        query = (
            "SELECT * FROM atoms WHERE " + " AND ".join(clauses) + " ORDER BY document_id, position"
        )
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        atoms = (self._atom(row) for row in rows)
        filtered = (
            atom
            for atom in atoms
            if atom.occurred_at is not None
            and (occurred_from is None or atom.occurred_at >= occurred_from)
            and (occurred_to is None or atom.occurred_at < occurred_to)
        )
        return tuple(
            sorted(
                filtered,
                key=lambda atom: (atom.occurred_at, atom.document_id, atom.position),
            )
        )

    def atom_tags_for(self, atom_id: str) -> tuple[AtomTag, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM atom_tags
                WHERE atom_id = ?
                ORDER BY tag_id
                """,
                (atom_id,),
            ).fetchall()
        return tuple(self._atom_tag(row) for row in rows)

    def persist_ingestion(self, bundle: IngestionBundle) -> None:
        """Persist the whole bundle in one transaction or persist nothing."""
        with self._lock, self._connection:
            self._upsert_document(bundle.document)
            self._upsert_atoms(bundle.atoms)
            self._upsert_tags(bundle.tags)
            self._upsert_atom_tags(bundle.atom_tags)
            self._upsert_atom_links(bundle.atom_links)

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> SQLiteRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def document_count(self) -> int:
        return self._count("documents")

    @property
    def atom_count(self) -> int:
        return self._count("atoms")

    @property
    def tag_count(self) -> int:
        return self._count("tags")

    @property
    def atom_tag_count(self) -> int:
        return self._count("atom_tags")

    @property
    def atom_link_count(self) -> int:
        return self._count("atom_links")

    def _count(self, table: str) -> int:
        allowed = {"documents", "atoms", "tags", "atom_tags", "atom_links"}
        if table not in allowed:
            raise ValueError(f"unsupported table: {table}")
        with self._lock:
            row = self._connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()
        return int(row[0])

    def _upsert_document(self, document: Document) -> None:
        self._connection.execute(
            """
            INSERT INTO documents (
                document_id, namespace, source, content_hash, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(document_id) DO UPDATE SET
                namespace = excluded.namespace,
                source = excluded.source,
                content_hash = excluded.content_hash,
                metadata_json = excluded.metadata_json
            """,
            (
                document.document_id,
                document.namespace,
                document.source,
                document.content_hash,
                document.created_at.isoformat(),
                self._json(document.metadata),
            ),
        )

    def _upsert_atoms(self, atoms: Iterable[Atom]) -> None:
        self._connection.executemany(
            """
            INSERT INTO atoms (
                atom_id, document_id, namespace, position, char_start, char_end,
                content, content_hash, kind, occurred_at, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(atom_id) DO UPDATE SET
                document_id = excluded.document_id,
                namespace = excluded.namespace,
                position = excluded.position,
                char_start = excluded.char_start,
                char_end = excluded.char_end,
                content = excluded.content,
                content_hash = excluded.content_hash,
                kind = excluded.kind,
                occurred_at = excluded.occurred_at,
                metadata_json = excluded.metadata_json
            """,
            (
                (
                    atom.atom_id,
                    atom.document_id,
                    atom.namespace,
                    atom.position,
                    atom.char_start,
                    atom.char_end,
                    atom.content,
                    atom.content_hash,
                    atom.kind,
                    atom.occurred_at.isoformat() if atom.occurred_at else None,
                    atom.created_at.isoformat(),
                    self._json(atom.metadata),
                )
                for atom in atoms
            ),
        )

    def _upsert_tags(self, tags: Iterable[Tag]) -> None:
        self._connection.executemany(
            """
            INSERT INTO tags (
                tag_id, namespace, canonical_text, display_text, level, state,
                aliases_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(tag_id) DO UPDATE SET
                namespace = excluded.namespace,
                canonical_text = excluded.canonical_text,
                display_text = excluded.display_text,
                level = excluded.level,
                state = excluded.state,
                aliases_json = excluded.aliases_json
            """,
            (
                (
                    tag.tag_id,
                    tag.namespace,
                    tag.canonical_text,
                    tag.display_text,
                    tag.level,
                    tag.state,
                    self._json(tag.aliases),
                    tag.created_at.isoformat(),
                )
                for tag in tags
            ),
        )

    def _upsert_atom_tags(self, atom_tags: Iterable[AtomTag]) -> None:
        self._connection.executemany(
            """
            INSERT INTO atom_tags (
                atom_id, tag_id, weight_raw, confidence, origin,
                evidence_sources_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(atom_id, tag_id) DO UPDATE SET
                weight_raw = excluded.weight_raw,
                confidence = excluded.confidence,
                origin = excluded.origin,
                evidence_sources_json = excluded.evidence_sources_json
            """,
            (
                (
                    edge.atom_id,
                    edge.tag_id,
                    edge.weight_raw,
                    edge.confidence,
                    edge.origin,
                    self._json(edge.evidence_sources),
                    edge.created_at.isoformat(),
                )
                for edge in atom_tags
            ),
        )

    def _upsert_atom_links(self, atom_links: Iterable[AtomLink]) -> None:
        self._connection.executemany(
            """
            INSERT INTO atom_links (
                from_atom_id, to_atom_id, relation, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(from_atom_id, to_atom_id, relation) DO UPDATE SET
                metadata_json = excluded.metadata_json
            """,
            (
                (
                    edge.from_atom_id,
                    edge.to_atom_id,
                    edge.relation,
                    edge.created_at.isoformat(),
                    self._json(edge.metadata),
                )
                for edge in atom_links
            ),
        )

    @staticmethod
    def _document(row: sqlite3.Row) -> Document:
        return Document(
            document_id=row["document_id"],
            namespace=row["namespace"],
            source=row["source"],
            content_hash=row["content_hash"],
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=SQLiteRepository._object(row["metadata_json"]),
        )

    @staticmethod
    def _atom(row: sqlite3.Row) -> Atom:
        return Atom(
            atom_id=row["atom_id"],
            document_id=row["document_id"],
            namespace=row["namespace"],
            position=row["position"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            content=row["content"],
            content_hash=row["content_hash"],
            kind=AtomKind(row["kind"]),
            occurred_at=(
                datetime.fromisoformat(row["occurred_at"]) if row["occurred_at"] else None
            ),
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=SQLiteRepository._object(row["metadata_json"]),
        )

    @staticmethod
    def _tag(row: sqlite3.Row) -> Tag:
        return Tag(
            tag_id=row["tag_id"],
            namespace=row["namespace"],
            canonical_text=row["canonical_text"],
            display_text=row["display_text"],
            level=TagLevel(row["level"]),
            state=TagState(row["state"]),
            aliases=tuple(SQLiteRepository._array(row["aliases_json"])),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _atom_tag(row: sqlite3.Row) -> AtomTag:
        return AtomTag(
            atom_id=row["atom_id"],
            tag_id=row["tag_id"],
            weight_raw=row["weight_raw"],
            confidence=row["confidence"],
            origin=TagOrigin(row["origin"]),
            evidence_sources=tuple(SQLiteRepository._array(row["evidence_sources_json"])),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _atom_link(row: sqlite3.Row) -> AtomLink:
        return AtomLink(
            from_atom_id=row["from_atom_id"],
            to_atom_id=row["to_atom_id"],
            relation=AtomLinkRelation(row["relation"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            metadata=SQLiteRepository._object(row["metadata_json"]),
        )

    @staticmethod
    def _json(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)

    @staticmethod
    def _object(value: str) -> dict[str, Any]:
        parsed = json.loads(value)
        if not isinstance(parsed, dict):
            raise ValueError("expected a JSON object")
        return parsed

    @staticmethod
    def _array(value: str) -> list[Any]:
        parsed = json.loads(value)
        if not isinstance(parsed, list):
            raise ValueError("expected a JSON array")
        return parsed

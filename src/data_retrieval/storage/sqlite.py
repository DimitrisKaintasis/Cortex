from __future__ import annotations

import json
import math
import re
import sqlite3
from collections.abc import Iterable, Iterator
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
    TagRelation,
    TagState,
)
from data_retrieval.retrieval.embedding import cosine_similarity
from data_retrieval.retrieval.models import AtomEmbedding, SearchHit

TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", re.UNICODE)


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
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(atom_id, tag_id)
                );

                CREATE TABLE IF NOT EXISTS atom_links (
                    from_atom_id TEXT NOT NULL REFERENCES atoms(atom_id),
                    to_atom_id TEXT NOT NULL REFERENCES atoms(atom_id),
                    relation TEXT NOT NULL,
                    weight_raw REAL NOT NULL CHECK(weight_raw >= 0),
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    evidence_sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(from_atom_id, to_atom_id, relation)
                );

                CREATE TABLE IF NOT EXISTS tag_relations (
                    source_tag_id TEXT NOT NULL REFERENCES tags(tag_id),
                    target_tag_id TEXT NOT NULL REFERENCES tags(tag_id),
                    relation_type TEXT NOT NULL,
                    weight_raw REAL NOT NULL CHECK(weight_raw >= 0),
                    confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
                    evidence_sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(source_tag_id, target_tag_id, relation_type)
                );

                CREATE TABLE IF NOT EXISTS retrieval_events (
                    retrieval_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS feedback_events (
                    feedback_id TEXT PRIMARY KEY,
                    retrieval_id TEXT NOT NULL REFERENCES retrieval_events(retrieval_id),
                    created_at TEXT NOT NULL,
                    payload_json TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS atom_embeddings (
                    atom_id TEXT NOT NULL REFERENCES atoms(atom_id),
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL CHECK(dimensions > 0),
                    vector_json TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(atom_id, provider, model)
                );

                CREATE INDEX IF NOT EXISTS idx_atoms_document_position
                    ON atoms(document_id, position);
                CREATE INDEX IF NOT EXISTS idx_atoms_namespace_occurred
                    ON atoms(namespace, occurred_at);
                CREATE INDEX IF NOT EXISTS idx_tags_namespace_canonical
                    ON tags(namespace, canonical_text);
                CREATE INDEX IF NOT EXISTS idx_atom_links_to
                    ON atom_links(to_atom_id, relation);
                CREATE INDEX IF NOT EXISTS idx_atom_embeddings_provider_model
                    ON atom_embeddings(provider, model);
                CREATE INDEX IF NOT EXISTS idx_tag_relations_source
                    ON tag_relations(source_tag_id, relation_type);
                CREATE INDEX IF NOT EXISTS idx_feedback_retrieval
                    ON feedback_events(retrieval_id);
                """
            )
            self._ensure_column("atom_tags", "updated_at", "TEXT")
            self._ensure_column("atom_links", "weight_raw", "REAL NOT NULL DEFAULT 1.0")
            self._ensure_column("atom_links", "confidence", "REAL NOT NULL DEFAULT 1.0")
            self._ensure_column("atom_links", "evidence_sources_json", "TEXT NOT NULL DEFAULT '[]'")
            self._ensure_column("atom_links", "updated_at", "TEXT")
            self._connection.execute(
                "UPDATE atom_tags SET updated_at = created_at WHERE updated_at IS NULL"
            )
            self._connection.execute(
                "UPDATE atom_links SET updated_at = created_at WHERE updated_at IS NULL"
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

    def get_document_atom_count(self, document_id: str) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT COUNT(*) FROM atoms WHERE document_id = ?", (document_id,)
            ).fetchone()
        return int(row[0])

    def get_atom(self, atom_id: str) -> Atom | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM atoms WHERE atom_id = ?", (atom_id,)
            ).fetchone()
        return self._atom(row) if row else None

    def get_atoms(self, atom_ids: tuple[str, ...]) -> tuple[Atom, ...]:
        if not atom_ids:
            return ()
        found: dict[str, Atom] = {}
        with self._lock:
            for batch in self._batches(atom_ids):
                placeholders = ",".join("?" for _ in batch)
                rows = self._connection.execute(
                    f"SELECT * FROM atoms WHERE atom_id IN ({placeholders})", batch
                ).fetchall()
                found.update((row["atom_id"], self._atom(row)) for row in rows)
        return tuple(found[atom_id] for atom_id in atom_ids if atom_id in found)

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

    def list_atom_links(
        self,
        *,
        namespace: str,
        relation: str | None = None,
    ) -> tuple[AtomLink, ...]:
        parameters: list[Any] = [namespace]
        relation_clause = ""
        if relation is not None:
            relation_clause = "AND links.relation = ?"
            parameters.append(relation)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT links.* FROM atom_links AS links
                JOIN atoms AS source_atom
                  ON source_atom.atom_id = links.from_atom_id
                WHERE source_atom.namespace = ? {relation_clause}
                ORDER BY links.relation, links.from_atom_id, links.to_atom_id
                """,
                parameters,
            ).fetchall()
        return tuple(self._atom_link(row) for row in rows)

    def get_atom_links_touching(
        self,
        *,
        atom_ids: tuple[str, ...],
        relation: str | None = None,
    ) -> tuple[AtomLink, ...]:
        if not atom_ids:
            return ()
        found: dict[tuple[str, str, str], AtomLink] = {}
        with self._lock:
            for batch in self._batches(atom_ids):
                placeholders = ",".join("?" for _ in batch)
                parameters: list[Any] = [*batch, *batch]
                relation_clause = ""
                if relation is not None:
                    relation_clause = "AND relation = ?"
                    parameters.append(str(relation))
                rows = self._connection.execute(
                    f"""
                    SELECT * FROM atom_links
                    WHERE (from_atom_id IN ({placeholders})
                           OR to_atom_id IN ({placeholders}))
                      {relation_clause}
                    """,
                    parameters,
                ).fetchall()
                for row in rows:
                    link = self._atom_link(row)
                    found[(link.from_atom_id, link.to_atom_id, link.relation)] = link
        return tuple(
            sorted(
                found.values(),
                key=lambda item: (item.relation, item.from_atom_id, item.to_atom_id),
            )
        )

    def list_tags(self, namespace: str, limit: int | None = None) -> tuple[Tag, ...]:
        limit_clause = "" if limit is None else "LIMIT ?"
        parameters: list[Any] = [namespace]
        if limit is not None:
            if limit <= 0:
                return ()
            parameters.append(limit)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT * FROM tags
                WHERE namespace = ?
                ORDER BY canonical_text
                {limit_clause}
                """,
                parameters,
            ).fetchall()
        return tuple(self._tag(row) for row in rows)

    def get_tags(self, tag_ids: tuple[str, ...]) -> tuple[Tag, ...]:
        if not tag_ids:
            return ()
        found: dict[str, Tag] = {}
        with self._lock:
            for batch in self._batches(tag_ids):
                placeholders = ",".join("?" for _ in batch)
                rows = self._connection.execute(
                    f"SELECT * FROM tags WHERE tag_id IN ({placeholders})", batch
                ).fetchall()
                found.update((row["tag_id"], self._tag(row)) for row in rows)
        return tuple(found[tag_id] for tag_id in tag_ids if tag_id in found)

    def get_tags_by_canonical(
        self, *, namespace: str, canonical_texts: tuple[str, ...]
    ) -> tuple[Tag, ...]:
        if not canonical_texts:
            return ()
        found: dict[str, Tag] = {}
        with self._lock:
            for batch in self._batches(canonical_texts):
                placeholders = ",".join("?" for _ in batch)
                rows = self._connection.execute(
                    f"""
                    SELECT * FROM tags
                    WHERE namespace = ? AND canonical_text IN ({placeholders})
                    """,
                    [namespace, *batch],
                ).fetchall()
                found.update((row["canonical_text"], self._tag(row)) for row in rows)
        return tuple(found[value] for value in canonical_texts if value in found)

    def list_tag_relations(
        self, *, namespace: str, relation_type: str | None = None
    ) -> tuple[TagRelation, ...]:
        parameters: list[Any] = [namespace]
        relation_clause = ""
        if relation_type is not None:
            relation_clause = "AND relations.relation_type = ?"
            parameters.append(relation_type)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT relations.* FROM tag_relations AS relations
                JOIN tags AS source_tag
                  ON source_tag.tag_id = relations.source_tag_id
                WHERE source_tag.namespace = ? {relation_clause}
                ORDER BY relations.relation_type, relations.source_tag_id,
                         relations.target_tag_id
                """,
                parameters,
            ).fetchall()
        return tuple(self._tag_relation(row) for row in rows)

    def get_tag_relations_touching(
        self,
        *,
        tag_ids: tuple[str, ...],
        relation_type: str | None = None,
    ) -> tuple[TagRelation, ...]:
        if not tag_ids:
            return ()
        found: dict[tuple[str, str, str], TagRelation] = {}
        with self._lock:
            for batch in self._batches(tag_ids):
                placeholders = ",".join("?" for _ in batch)
                parameters: list[Any] = [*batch, *batch]
                relation_clause = ""
                if relation_type is not None:
                    relation_clause = "AND relation_type = ?"
                    parameters.append(relation_type)
                rows = self._connection.execute(
                    f"""
                    SELECT * FROM tag_relations
                    WHERE (source_tag_id IN ({placeholders})
                           OR target_tag_id IN ({placeholders}))
                      {relation_clause}
                    """,
                    parameters,
                ).fetchall()
                for row in rows:
                    edge = self._tag_relation(row)
                    found[(edge.source_tag_id, edge.target_tag_id, edge.relation_type)] = edge
        return tuple(
            sorted(
                found.values(),
                key=lambda item: (item.relation_type, item.source_tag_id, item.target_tag_id),
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
        clauses = ["namespace = ?"]
        parameters: list[Any] = [namespace]
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind.value)
        if occurred_from is not None:
            clauses.append("julianday(occurred_at) >= julianday(?)")
            parameters.append(occurred_from.isoformat())
        if occurred_to is not None:
            clauses.append("julianday(occurred_at) < julianday(?)")
            parameters.append(occurred_to.isoformat())
        query = (
            "SELECT * FROM atoms WHERE " + " AND ".join(clauses) + " ORDER BY document_id, position"
        )
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
        return tuple(
            sorted(
                (self._atom(row) for row in rows),
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
        clauses = ["namespace = ?"]
        parameters: list[Any] = [namespace]
        if kind is not None:
            clauses.append("kind = ?")
            parameters.append(kind.value)
        if occurred_from is not None:
            clauses.append("julianday(occurred_at) >= julianday(?)")
            parameters.append(occurred_from.isoformat())
        if occurred_to is not None:
            clauses.append("julianday(occurred_at) < julianday(?)")
            parameters.append(occurred_to.isoformat())
        query = (
            "SELECT * FROM atoms WHERE "
            + " AND ".join(clauses)
            + " ORDER BY document_id, position"
        )
        with self._lock:
            cursor = self._connection.execute(query, parameters)
            while rows := cursor.fetchmany(batch_size):
                yield tuple(self._atom(row) for row in rows)

    def search_tag_hits(
        self,
        *,
        namespace: str,
        canonical_tags: tuple[str, ...],
        limit: int,
    ) -> tuple[SearchHit, ...]:
        if not canonical_tags or limit <= 0:
            return ()
        placeholders = ",".join("?" for _ in canonical_tags)
        with self._lock:
            rows = self._connection.execute(
                f"""
                SELECT edges.atom_id, tags.canonical_text, edges.weight_raw,
                       edges.confidence
                FROM atom_tags AS edges
                JOIN tags ON tags.tag_id = edges.tag_id
                JOIN atoms ON atoms.atom_id = edges.atom_id
                WHERE atoms.namespace = ?
                  AND tags.canonical_text IN ({placeholders})
                """,
                [namespace, *canonical_tags],
            ).fetchall()
        scores: dict[str, float] = {}
        evidence: dict[str, list[str]] = {}
        for row in rows:
            scores[row["atom_id"]] = scores.get(row["atom_id"], 0.0) + (
                row["confidence"] * math.log1p(row["weight_raw"]) / math.log(2.0)
            ) / len(canonical_tags)
            evidence.setdefault(row["atom_id"], []).append(f"tag={row['canonical_text']}")
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
        for batch in self.iter_atoms(namespace=namespace):
            for atom in batch:
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
        hits: list[SearchHit] = []
        for atoms in self.iter_atoms(namespace=namespace, batch_size=500):
            embeddings = self.get_embeddings(
                atom_ids=tuple(atom.atom_id for atom in atoms),
                provider=provider,
                model=model,
            )
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
        with self._lock, self._connection:
            self._connection.executemany(
                """
                INSERT INTO atom_embeddings (
                    atom_id, provider, model, dimensions, vector_json,
                    content_hash, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(atom_id, provider, model) DO UPDATE SET
                    dimensions = excluded.dimensions,
                    vector_json = excluded.vector_json,
                    content_hash = excluded.content_hash,
                    created_at = excluded.created_at
                """,
                (
                    (
                        item.atom_id,
                        item.provider,
                        item.model,
                        item.dimensions,
                        self._json(item.vector),
                        item.content_hash,
                        item.created_at.isoformat(),
                    )
                    for item in embeddings
                ),
            )

    def get_embeddings(
        self,
        *,
        atom_ids: tuple[str, ...],
        provider: str,
        model: str,
    ) -> dict[str, AtomEmbedding]:
        if not atom_ids:
            return {}
        rows: list[sqlite3.Row] = []
        with self._lock:
            for offset in range(0, len(atom_ids), 500):
                batch = atom_ids[offset : offset + 500]
                placeholders = ",".join("?" for _ in batch)
                parameters: list[Any] = [*batch, provider, model]
                rows.extend(
                    self._connection.execute(
                        f"""
                        SELECT * FROM atom_embeddings
                        WHERE atom_id IN ({placeholders})
                          AND provider = ? AND model = ?
                        """,
                        parameters,
                    ).fetchall()
                )
        return {
            row["atom_id"]: AtomEmbedding(
                atom_id=row["atom_id"],
                provider=row["provider"],
                model=row["model"],
                dimensions=row["dimensions"],
                vector=tuple(float(value) for value in self._array(row["vector_json"])),
                content_hash=row["content_hash"],
                created_at=datetime.fromisoformat(row["created_at"]),
            )
            for row in rows
        }

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

    def list_atom_tags(self, namespace: str) -> tuple[AtomTag, ...]:
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT edges.* FROM atom_tags AS edges
                JOIN atoms ON atoms.atom_id = edges.atom_id
                WHERE atoms.namespace = ?
                ORDER BY edges.atom_id, edges.tag_id
                """,
                (namespace,),
            ).fetchall()
        return tuple(self._atom_tag(row) for row in rows)

    def get_atom_tags_for_atoms(self, atom_ids: tuple[str, ...]) -> tuple[AtomTag, ...]:
        if not atom_ids:
            return ()
        found: dict[tuple[str, str], AtomTag] = {}
        with self._lock:
            for batch in self._batches(atom_ids):
                placeholders = ",".join("?" for _ in batch)
                rows = self._connection.execute(
                    f"SELECT * FROM atom_tags WHERE atom_id IN ({placeholders})", batch
                ).fetchall()
                for row in rows:
                    edge = self._atom_tag(row)
                    found[(edge.atom_id, edge.tag_id)] = edge
        return tuple(sorted(found.values(), key=lambda item: (item.atom_id, item.tag_id)))

    def record_retrieval_event(self, event: dict[str, object]) -> None:
        with self._lock, self._connection:
            self._connection.execute(
                """
                INSERT INTO retrieval_events (
                    retrieval_id, namespace, created_at, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    str(event["retrieval_id"]),
                    str(event["namespace"]),
                    str(event["created_at"]),
                    self._json(event),
                ),
            )

    def get_retrieval_event(self, retrieval_id: str) -> dict[str, object] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT payload_json FROM retrieval_events WHERE retrieval_id = ?",
                (retrieval_id,),
            ).fetchone()
        return self._object(row["payload_json"]) if row else None

    def apply_learning_updates(
        self,
        *,
        feedback_event: dict[str, object],
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None:
        """Store one feedback event and all derived weight changes atomically."""
        with self._lock, self._connection:
            self._upsert_atom_tags(atom_tags)
            self._upsert_atom_links(atom_links)
            self._upsert_tag_relations(tag_relations)
            self._connection.execute(
                """
                INSERT INTO feedback_events (
                    feedback_id, retrieval_id, created_at, payload_json
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    str(feedback_event["feedback_id"]),
                    str(feedback_event["retrieval_id"]),
                    str(feedback_event["created_at"]),
                    self._json(feedback_event),
                ),
            )

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
                evidence_sources_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(atom_id, tag_id) DO UPDATE SET
                weight_raw = excluded.weight_raw,
                confidence = excluded.confidence,
                origin = excluded.origin,
                evidence_sources_json = excluded.evidence_sources_json,
                updated_at = excluded.updated_at
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
                    edge.updated_at.isoformat(),
                )
                for edge in atom_tags
            ),
        )

    def _upsert_atom_links(self, atom_links: Iterable[AtomLink]) -> None:
        self._connection.executemany(
            """
            INSERT INTO atom_links (
                from_atom_id, to_atom_id, relation, weight_raw, confidence,
                evidence_sources_json, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(from_atom_id, to_atom_id, relation) DO UPDATE SET
                weight_raw = excluded.weight_raw,
                confidence = excluded.confidence,
                evidence_sources_json = excluded.evidence_sources_json,
                updated_at = excluded.updated_at,
                metadata_json = excluded.metadata_json
            """,
            (
                (
                    edge.from_atom_id,
                    edge.to_atom_id,
                    edge.relation,
                    edge.weight_raw,
                    edge.confidence,
                    self._json(edge.evidence_sources),
                    edge.created_at.isoformat(),
                    edge.updated_at.isoformat(),
                    self._json(edge.metadata),
                )
                for edge in atom_links
            ),
        )

    def _upsert_tag_relations(self, relations: Iterable[TagRelation]) -> None:
        self._connection.executemany(
            """
            INSERT INTO tag_relations (
                source_tag_id, target_tag_id, relation_type, weight_raw,
                confidence, evidence_sources_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_tag_id, target_tag_id, relation_type) DO UPDATE SET
                weight_raw = excluded.weight_raw,
                confidence = excluded.confidence,
                evidence_sources_json = excluded.evidence_sources_json,
                updated_at = excluded.updated_at
            """,
            (
                (
                    edge.source_tag_id,
                    edge.target_tag_id,
                    edge.relation_type,
                    edge.weight_raw,
                    edge.confidence,
                    self._json(edge.evidence_sources),
                    edge.created_at.isoformat(),
                    edge.updated_at.isoformat(),
                )
                for edge in relations
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
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    @staticmethod
    def _atom_link(row: sqlite3.Row) -> AtomLink:
        return AtomLink(
            from_atom_id=row["from_atom_id"],
            to_atom_id=row["to_atom_id"],
            relation=AtomLinkRelation(row["relation"]),
            weight_raw=row["weight_raw"],
            confidence=row["confidence"],
            evidence_sources=tuple(SQLiteRepository._array(row["evidence_sources_json"])),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=SQLiteRepository._object(row["metadata_json"]),
        )

    @staticmethod
    def _tag_relation(row: sqlite3.Row) -> TagRelation:
        return TagRelation(
            source_tag_id=row["source_tag_id"],
            target_tag_id=row["target_tag_id"],
            relation_type=row["relation_type"],
            weight_raw=row["weight_raw"],
            confidence=row["confidence"],
            evidence_sources=tuple(SQLiteRepository._array(row["evidence_sources_json"])),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def _ensure_column(self, table: str, column: str, definition: str) -> None:
        columns = {
            row["name"]
            for row in self._connection.execute(f"PRAGMA table_info({table})").fetchall()
        }
        if column not in columns:
            self._connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")

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

    @staticmethod
    def _batches(values: tuple[str, ...], size: int = 500) -> Iterator[tuple[str, ...]]:
        for offset in range(0, len(values), size):
            yield values[offset : offset + size]

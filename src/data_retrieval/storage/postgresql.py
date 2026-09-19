from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Iterator
from datetime import datetime
from threading import RLock
from typing import Any

import psycopg
from pgvector import Vector
from pgvector.psycopg import register_vector
from psycopg import sql
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from data_retrieval.connectors.codec import (
    record_from_mapping,
    record_to_mapping,
    relation_from_mapping,
    relation_to_mapping,
    source_from_mapping,
    source_to_mapping,
    sync_batch_acknowledgement_from_mapping,
    sync_batch_acknowledgement_to_mapping,
    sync_commit_acknowledgement_from_mapping,
    sync_commit_acknowledgement_to_mapping,
    sync_run_from_mapping,
    sync_run_to_mapping,
    tombstone_to_mapping,
)
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
)
from data_retrieval.core.weight_events import (
    EdgeCoordinates,
    calibration_transition_events,
    edge_coordinates,
    transition_events,
)
from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    AtomRole,
    AtomTag,
    CalibrationSignal,
    CalibrationTarget,
    Document,
    IngestionBundle,
    PayloadModality,
    Tag,
    TagCandidate,
    TagCandidateState,
    TagLevel,
    TagOrigin,
    TagRelation,
    TagState,
    WeightEvent,
    WeightEventSource,
)
from data_retrieval.retrieval.models import AtomEmbedding, SearchHit
from data_retrieval.storage.migrations import (
    BASELINE_SCHEMA,
    CANONICAL_TABLES,
    CONNECTOR_LIFECYCLE_SCHEMA,
    CONNECTOR_PROJECTION_SCHEMA,
    CONNECTOR_PROJECTION_TABLES,
    CONNECTOR_TABLES,
    CURRENT_SCHEMA_VERSION,
    pending_migrations,
)

SCHEMA = "data_retrieval"
HNSW_MAX_VECTOR_DIMENSIONS = 2_000


class PostgreSQLRepository:
    """PostgreSQL/pgvector canonical adapter for online and large deployments."""

    def __init__(self, dsn: str, *, initialize: bool = True) -> None:
        if not dsn.strip():
            raise ValueError("PostgreSQL DSN cannot be empty")
        self._connection = psycopg.connect(dsn, autocommit=True, row_factory=dict_row)
        self._lock = RLock()
        self._indexed_embeddings: set[tuple[str, str, int]] = set()
        if initialize:
            self.initialize_schema()
        else:
            register_vector(self._connection)

    def initialize_schema(self) -> None:
        with self._lock:
            self._ensure_schema_metadata()
            current_version = self.schema_version
            for migration in pending_migrations(current_version):
                if migration == BASELINE_SCHEMA:
                    self._apply_baseline_schema()
                elif migration == CONNECTOR_LIFECYCLE_SCHEMA:
                    self._apply_connector_lifecycle_schema()
                elif migration == CONNECTOR_PROJECTION_SCHEMA:
                    self._apply_connector_projection_schema()
                else:
                    raise RuntimeError(
                        f"missing PostgreSQL migration implementation: {migration.name}"
                    )
                current_version = migration.version
            self._validate_schema()
        register_vector(self._connection)

    def _ensure_schema_metadata(self) -> None:
        with self._connection.transaction():
            self._connection.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.schema_metadata (
                    singleton BOOLEAN PRIMARY KEY DEFAULT TRUE CHECK(singleton),
                    version INTEGER NOT NULL CHECK(version >= 0),
                    migration_name TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            self._connection.execute(
                f"""
                INSERT INTO {SCHEMA}.schema_metadata (
                    singleton, version, migration_name
                ) VALUES (TRUE, 0, 'unversioned')
                ON CONFLICT(singleton) DO NOTHING
                """
            )

    @property
    def schema_version(self) -> int:
        row = self._connection.execute(
            f"SELECT version FROM {SCHEMA}.schema_metadata WHERE singleton = TRUE"
        ).fetchone()
        if row is None:
            raise RuntimeError("PostgreSQL schema metadata row is missing")
        return int(row["version"])

    def _record_schema_version(self, migration=BASELINE_SCHEMA) -> None:
        self._connection.execute(
            f"""
            UPDATE {SCHEMA}.schema_metadata
            SET version = %s, migration_name = %s, updated_at = NOW()
            WHERE singleton = TRUE
            """,
            (migration.version, migration.name),
        )

    def _validate_schema(self) -> None:
        self._validate_schema_structure()
        self._validate_connector_schema_structure()
        self._validate_connector_projection_schema_structure()
        if self.schema_version != CURRENT_SCHEMA_VERSION:
            raise RuntimeError(
                f"PostgreSQL schema version is {self.schema_version}, expected "
                f"{CURRENT_SCHEMA_VERSION}"
            )

    def _validate_schema_structure(self) -> None:
        rows = self._connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (SCHEMA,),
        ).fetchall()
        actual_tables = {str(row["table_name"]) for row in rows}
        missing_tables = sorted((CANONICAL_TABLES | {"schema_metadata"}) - actual_tables)
        if missing_tables:
            raise RuntimeError(
                "PostgreSQL schema invariant failed; missing tables: " + ", ".join(missing_tables)
            )
        required_columns = {
            "documents": {"ingestion_status", "atom_count"},
            "atoms": {"role", "modality"},
            "schema_metadata": {"version", "migration_name", "updated_at"},
        }
        for table, expected_columns in required_columns.items():
            column_rows = self._connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema = %s AND table_name = %s",
                (SCHEMA, table),
            ).fetchall()
            actual_columns = {str(row["column_name"]) for row in column_rows}
            missing_columns = sorted(expected_columns - actual_columns)
            if missing_columns:
                raise RuntimeError(
                    f"PostgreSQL schema invariant failed; {table} is missing columns: "
                    + ", ".join(missing_columns)
                )

    def _validate_connector_schema_structure(self) -> None:
        rows = self._connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (SCHEMA,),
        ).fetchall()
        actual_tables = {str(row["table_name"]) for row in rows}
        missing_tables = sorted(CONNECTOR_TABLES - actual_tables)
        if missing_tables:
            raise RuntimeError(
                "PostgreSQL connector schema invariant failed; missing tables: "
                + ", ".join(missing_tables)
            )

    def _validate_connector_projection_schema_structure(self) -> None:
        rows = self._connection.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
            (SCHEMA,),
        ).fetchall()
        actual_tables = {str(row["table_name"]) for row in rows}
        missing_tables = sorted(CONNECTOR_PROJECTION_TABLES - actual_tables)
        if missing_tables:
            raise RuntimeError(
                "PostgreSQL connector projection schema invariant failed; missing tables: "
                + ", ".join(missing_tables)
            )
        column = self._connection.execute(
            """
            SELECT 1 FROM information_schema.columns
            WHERE table_schema = %s AND table_name = 'connector_sync_batches'
              AND column_name = 'payload_json'
            """,
            (SCHEMA,),
        ).fetchone()
        if column is None:
            raise RuntimeError(
                "PostgreSQL connector projection schema invariant failed; "
                "connector_sync_batches.payload_json is missing"
            )

    def _apply_baseline_schema(self) -> None:
        with self._connection.transaction():
            self._connection.execute("CREATE EXTENSION IF NOT EXISTS vector")
            self._connection.execute(f"CREATE SCHEMA IF NOT EXISTS {SCHEMA}")
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.documents (
                    document_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    ingestion_status TEXT NOT NULL DEFAULT 'complete'
                        CHECK (ingestion_status IN ('staging', 'complete')),
                    atom_count BIGINT NOT NULL DEFAULT 0 CHECK (atom_count >= 0)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.atoms (
                    atom_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL
                        REFERENCES {SCHEMA}.documents(document_id) ON DELETE CASCADE,
                    namespace TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    char_start BIGINT NOT NULL,
                    char_end BIGINT NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'source',
                    modality TEXT NOT NULL DEFAULT 'text',
                    occurred_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    search_vector TSVECTOR GENERATED ALWAYS AS (
                        to_tsvector('simple', content)
                    ) STORED,
                    UNIQUE(document_id, position)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.tags (
                    tag_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    canonical_text TEXT NOT NULL,
                    display_text TEXT NOT NULL,
                    level TEXT NOT NULL,
                    state TEXT NOT NULL,
                    aliases TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    created_at TIMESTAMPTZ NOT NULL,
                    UNIQUE(namespace, canonical_text)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.atom_tags (
                    atom_id TEXT NOT NULL REFERENCES {SCHEMA}.atoms(atom_id) ON DELETE CASCADE,
                    tag_id TEXT NOT NULL REFERENCES {SCHEMA}.tags(tag_id) ON DELETE CASCADE,
                    weight_raw DOUBLE PRECISION NOT NULL CHECK(weight_raw >= 0),
                    confidence DOUBLE PRECISION NOT NULL
                        CHECK(confidence >= 0 AND confidence <= 1),
                    origin TEXT NOT NULL,
                    evidence_sources TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(atom_id, tag_id)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.tag_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    atom_id TEXT NOT NULL
                        REFERENCES {SCHEMA}.atoms(atom_id) ON DELETE CASCADE,
                    normalized_text TEXT NOT NULL,
                    display_text TEXT NOT NULL,
                    level TEXT NOT NULL,
                    confidence DOUBLE PRECISION NOT NULL
                        CHECK(confidence >= 0 AND confidence <= 1),
                    state TEXT NOT NULL,
                    producer TEXT NOT NULL,
                    proposal_version TEXT NOT NULL,
                    resolved_tag_id TEXT REFERENCES {SCHEMA}.tags(tag_id),
                    resolution_reason TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    resolved_at TIMESTAMPTZ,
                    UNIQUE(atom_id, normalized_text, producer, proposal_version)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.atom_links (
                    from_atom_id TEXT NOT NULL
                        REFERENCES {SCHEMA}.atoms(atom_id) ON DELETE CASCADE,
                    to_atom_id TEXT NOT NULL
                        REFERENCES {SCHEMA}.atoms(atom_id) ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    weight_raw DOUBLE PRECISION NOT NULL CHECK(weight_raw >= 0),
                    confidence DOUBLE PRECISION NOT NULL
                        CHECK(confidence >= 0 AND confidence <= 1),
                    evidence_sources TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                    PRIMARY KEY(from_atom_id, to_atom_id, relation)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.tag_relations (
                    source_tag_id TEXT NOT NULL
                        REFERENCES {SCHEMA}.tags(tag_id) ON DELETE CASCADE,
                    target_tag_id TEXT NOT NULL
                        REFERENCES {SCHEMA}.tags(tag_id) ON DELETE CASCADE,
                    relation_type TEXT NOT NULL,
                    weight_raw DOUBLE PRECISION NOT NULL CHECK(weight_raw >= 0),
                    confidence DOUBLE PRECISION NOT NULL
                        CHECK(confidence >= 0 AND confidence <= 1),
                    evidence_sources TEXT[] NOT NULL DEFAULT ARRAY[]::TEXT[],
                    created_at TIMESTAMPTZ NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(source_tag_id, target_tag_id, relation_type),
                    CHECK(source_tag_id <> target_tag_id)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.atom_embeddings (
                    atom_id TEXT NOT NULL REFERENCES {SCHEMA}.atoms(atom_id) ON DELETE CASCADE,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    dimensions INTEGER NOT NULL CHECK(dimensions > 0),
                    embedding VECTOR NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(atom_id, provider, model),
                    CHECK(vector_dims(embedding) = dimensions)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.retrieval_events (
                    retrieval_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    payload_json JSONB NOT NULL
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.feedback_events (
                    feedback_id TEXT PRIMARY KEY,
                    retrieval_id TEXT NOT NULL
                        REFERENCES {SCHEMA}.retrieval_events(retrieval_id),
                    created_at TIMESTAMPTZ NOT NULL,
                    payload_json JSONB NOT NULL
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.calibration_signals (
                    signal_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    related_id TEXT,
                    relation_type TEXT,
                    signal_type TEXT NOT NULL,
                    value DOUBLE PRECISION NOT NULL CHECK(value >= 0 AND value <= 1),
                    confidence DOUBLE PRECISION NOT NULL
                        CHECK(confidence >= 0 AND confidence <= 1),
                    multiplier DOUBLE PRECISION NOT NULL CHECK(multiplier > 0),
                    provider TEXT NOT NULL,
                    profile_version TEXT NOT NULL,
                    source_reference TEXT,
                    created_at TIMESTAMPTZ NOT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE IF NOT EXISTS {SCHEMA}.weight_events (
                    event_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    target_type TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    related_id TEXT NOT NULL,
                    relation_type TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    policy_version TEXT NOT NULL,
                    weight_before DOUBLE PRECISION NOT NULL CHECK(weight_before >= 0),
                    weight_after DOUBLE PRECISION NOT NULL CHECK(weight_after >= 0),
                    delta DOUBLE PRECISION NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL,
                    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb
                )
                """
            )
            self._connection.execute(
                f"ALTER TABLE {SCHEMA}.documents ADD COLUMN IF NOT EXISTS "
                "ingestion_status TEXT NOT NULL DEFAULT 'complete' "
                "CHECK (ingestion_status IN ('staging', 'complete'))"
            )
            self._connection.execute(
                f"ALTER TABLE {SCHEMA}.documents ADD COLUMN IF NOT EXISTS "
                "atom_count BIGINT NOT NULL DEFAULT 0 CHECK (atom_count >= 0)"
            )
            self._connection.execute(
                f"""
                UPDATE {SCHEMA}.documents AS documents
                SET atom_count = (
                    SELECT COUNT(*) FROM {SCHEMA}.atoms AS atoms
                    WHERE atoms.document_id = documents.document_id
                )
                """
            )
            self._connection.execute(
                f"ALTER TABLE {SCHEMA}.atoms ADD COLUMN IF NOT EXISTS role TEXT"
            )
            self._connection.execute(
                f"ALTER TABLE {SCHEMA}.atoms ADD COLUMN IF NOT EXISTS modality TEXT"
            )
            self._connection.execute(
                f"""
                UPDATE {SCHEMA}.atoms
                SET role = CASE
                    WHEN metadata_json ->> 'source_system' = 'mem0' THEN 'derived'
                    WHEN kind = 'temporal_summary' THEN 'derived'
                    WHEN kind = 'interaction' THEN 'interaction'
                    WHEN kind = 'uncertainty' THEN 'uncertainty'
                    ELSE 'source'
                END
                WHERE role IS NULL
                   OR (
                        role = 'source'
                        AND (
                            kind <> 'source'
                            OR metadata_json ->> 'source_system' = 'mem0'
                        )
                   )
                """
            )
            self._connection.execute(
                f"UPDATE {SCHEMA}.atoms SET modality = 'text' WHERE modality IS NULL"
            )
            for statement in (
                f"ALTER TABLE {SCHEMA}.atoms ALTER COLUMN role SET DEFAULT 'source'",
                f"ALTER TABLE {SCHEMA}.atoms ALTER COLUMN role SET NOT NULL",
                f"ALTER TABLE {SCHEMA}.atoms ALTER COLUMN modality SET DEFAULT 'text'",
                f"ALTER TABLE {SCHEMA}.atoms ALTER COLUMN modality SET NOT NULL",
            ):
                self._connection.execute(statement)
            for statement in (
                f"CREATE INDEX IF NOT EXISTS idx_documents_namespace_status "
                f"ON {SCHEMA}.documents(namespace, ingestion_status)",
                f"CREATE INDEX IF NOT EXISTS idx_atoms_document_position "
                f"ON {SCHEMA}.atoms(document_id, position)",
                f"CREATE INDEX IF NOT EXISTS idx_atoms_namespace_occurred "
                f"ON {SCHEMA}.atoms(namespace, occurred_at)",
                f"CREATE INDEX IF NOT EXISTS idx_atoms_namespace_kind "
                f"ON {SCHEMA}.atoms(namespace, kind)",
                f"CREATE INDEX IF NOT EXISTS idx_atoms_namespace_role "
                f"ON {SCHEMA}.atoms(namespace, role)",
                f"CREATE INDEX IF NOT EXISTS idx_atoms_search_vector "
                f"ON {SCHEMA}.atoms USING GIN(search_vector)",
                f"CREATE INDEX IF NOT EXISTS idx_tags_namespace_canonical "
                f"ON {SCHEMA}.tags(namespace, canonical_text)",
                f"CREATE INDEX IF NOT EXISTS idx_tag_candidates_namespace_state "
                f"ON {SCHEMA}.tag_candidates(namespace, state, created_at)",
                f"CREATE INDEX IF NOT EXISTS idx_atom_tags_tag_atom "
                f"ON {SCHEMA}.atom_tags(tag_id, atom_id)",
                f"CREATE INDEX IF NOT EXISTS idx_atom_links_to_relation "
                f"ON {SCHEMA}.atom_links(to_atom_id, relation)",
                f"CREATE INDEX IF NOT EXISTS idx_tag_relations_target_type "
                f"ON {SCHEMA}.tag_relations(target_tag_id, relation_type)",
                f"CREATE INDEX IF NOT EXISTS idx_embeddings_provider_model "
                f"ON {SCHEMA}.atom_embeddings(provider, model, dimensions)",
                f"CREATE INDEX IF NOT EXISTS idx_feedback_retrieval "
                f"ON {SCHEMA}.feedback_events(retrieval_id)",
                f"CREATE INDEX IF NOT EXISTS idx_calibration_target "
                f"ON {SCHEMA}.calibration_signals(namespace, target_type, target_id)",
                f"CREATE INDEX IF NOT EXISTS idx_weight_events_target "
                f"ON {SCHEMA}.weight_events("
                "namespace, target_type, target_id, related_id, relation_type, created_at)",
            ):
                self._connection.execute(statement)
            self._migrate_legacy_proposed_tags()
            self._seed_weight_event_baselines()
            self._validate_schema_structure()
            self._record_schema_version()

    def _apply_connector_lifecycle_schema(self) -> None:
        with self._connection.transaction():
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_sources (
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    payload_json JSONB NOT NULL,
                    committed_cursor TEXT,
                    PRIMARY KEY(source_system, source_instance)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_sync_runs (
                    request_id TEXT PRIMARY KEY,
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    payload_json JSONB NOT NULL,
                    status TEXT NOT NULL DEFAULT 'running'
                        CHECK(status IN ('running', 'committed')),
                    commit_request_id TEXT UNIQUE,
                    commit_ack_json JSONB,
                    FOREIGN KEY(source_system, source_instance)
                        REFERENCES {SCHEMA}.connector_sources(
                            source_system, source_instance
                        )
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_sync_batches (
                    run_request_id TEXT NOT NULL
                        REFERENCES {SCHEMA}.connector_sync_runs(request_id),
                    batch_id TEXT NOT NULL,
                    sequence BIGINT NOT NULL CHECK(sequence >= 0),
                    fingerprint TEXT NOT NULL,
                    acknowledgement_json JSONB NOT NULL,
                    PRIMARY KEY(run_request_id, batch_id, sequence)
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_record_objects (
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    current_version TEXT,
                    current_observed_at TIMESTAMPTZ,
                    tombstone_version TEXT,
                    tombstoned_at TIMESTAMPTZ,
                    PRIMARY KEY(source_system, source_instance, external_id),
                    FOREIGN KEY(source_system, source_instance)
                        REFERENCES {SCHEMA}.connector_sources(
                            source_system, source_instance
                        )
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_records (
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    external_version TEXT NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL,
                    predecessor_version TEXT,
                    payload_json JSONB NOT NULL,
                    PRIMARY KEY(
                        source_system, source_instance, external_id, external_version
                    ),
                    FOREIGN KEY(source_system, source_instance, external_id)
                        REFERENCES {SCHEMA}.connector_record_objects(
                            source_system, source_instance, external_id
                        )
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_relations (
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    relation_id TEXT NOT NULL,
                    relation_version TEXT NOT NULL,
                    payload_json JSONB NOT NULL,
                    PRIMARY KEY(
                        source_system, source_instance, relation_id, relation_version
                    ),
                    FOREIGN KEY(source_system, source_instance)
                        REFERENCES {SCHEMA}.connector_sources(
                            source_system, source_instance
                        )
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_tombstones (
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    tombstone_version TEXT NOT NULL,
                    observed_at TIMESTAMPTZ NOT NULL,
                    payload_json JSONB NOT NULL,
                    PRIMARY KEY(
                        source_system, source_instance, external_id, tombstone_version
                    ),
                    FOREIGN KEY(source_system, source_instance, external_id)
                        REFERENCES {SCHEMA}.connector_record_objects(
                            source_system, source_instance, external_id
                        )
                )
                """
            )
            for statement in (
                f"CREATE INDEX idx_connector_records_current "
                f"ON {SCHEMA}.connector_records("
                "source_system, source_instance, external_id, observed_at)",
                f"CREATE INDEX idx_connector_relations_source "
                f"ON {SCHEMA}.connector_relations("
                "source_system, source_instance, relation_id)",
                f"CREATE INDEX idx_connector_tombstones_object "
                f"ON {SCHEMA}.connector_tombstones("
                "source_system, source_instance, external_id, observed_at)",
            ):
                self._connection.execute(statement)
            self._validate_connector_schema_structure()
            self._record_schema_version(CONNECTOR_LIFECYCLE_SCHEMA)

    def _apply_connector_projection_schema(self) -> None:
        with self._connection.transaction():
            self._connection.execute(
                f"ALTER TABLE {SCHEMA}.connector_sync_batches ADD COLUMN payload_json JSONB"
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_record_projections (
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    external_version TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    document_id TEXT NOT NULL UNIQUE
                        REFERENCES {SCHEMA}.documents(document_id),
                    projected_at TIMESTAMPTZ NOT NULL,
                    serving_state TEXT NOT NULL DEFAULT 'active'
                        CHECK(serving_state IN ('active', 'tombstoned')),
                    PRIMARY KEY(
                        source_system, source_instance, external_id, external_version
                    ),
                    FOREIGN KEY(
                        source_system, source_instance, external_id, external_version
                    ) REFERENCES {SCHEMA}.connector_records(
                        source_system, source_instance, external_id, external_version
                    )
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_projection_atoms (
                    atom_id TEXT PRIMARY KEY REFERENCES {SCHEMA}.atoms(atom_id),
                    evidence_id TEXT NOT NULL UNIQUE,
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    external_version TEXT NOT NULL,
                    FOREIGN KEY(
                        source_system, source_instance, external_id, external_version
                    ) REFERENCES {SCHEMA}.connector_record_projections(
                        source_system, source_instance, external_id, external_version
                    )
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_tombstone_projections (
                    source_system TEXT NOT NULL,
                    source_instance TEXT NOT NULL,
                    external_id TEXT NOT NULL,
                    tombstone_version TEXT NOT NULL,
                    applied_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY(
                        source_system, source_instance, external_id, tombstone_version
                    ),
                    FOREIGN KEY(
                        source_system, source_instance, external_id, tombstone_version
                    ) REFERENCES {SCHEMA}.connector_tombstones(
                        source_system, source_instance, external_id, tombstone_version
                    )
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_query_receipts (
                    request_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    retrieval_id TEXT NOT NULL UNIQUE,
                    context_json JSONB NOT NULL
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE TABLE {SCHEMA}.connector_outcome_receipts (
                    request_id TEXT PRIMARY KEY,
                    fingerprint TEXT NOT NULL,
                    acknowledgement_json JSONB NOT NULL
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE INDEX idx_connector_projection_record
                ON {SCHEMA}.connector_projection_atoms(
                    source_system, source_instance, external_id, external_version
                )
                """
            )
            self._connection.execute(
                f"""
                CREATE INDEX idx_connector_projection_serving
                ON {SCHEMA}.connector_record_projections(namespace, serving_state)
                """
            )
            self._validate_connector_projection_schema_structure()
            self._record_schema_version(CONNECTOR_PROJECTION_SCHEMA)

    def get_document(self, document_id: str) -> Document | None:
        row = self._fetchone(
            f"SELECT * FROM {SCHEMA}.documents "
            "WHERE document_id = %s AND ingestion_status = 'complete'",
            (document_id,),
        )
        return self._document(row) if row else None

    def list_namespaces(self, prefix: str | None = None) -> tuple[str, ...]:
        if prefix is None:
            rows = self._fetchall(
                f"SELECT DISTINCT namespace FROM {SCHEMA}.documents "
                "WHERE ingestion_status = 'complete' ORDER BY namespace"
            )
        else:
            rows = self._fetchall(
                f"SELECT DISTINCT namespace FROM {SCHEMA}.documents "
                "WHERE ingestion_status = 'complete' AND namespace LIKE %s "
                "ORDER BY namespace",
                (f"{self._escape_like(prefix)}%",),
            )
        return tuple(str(row["namespace"]) for row in rows)

    def get_documents(self, document_ids: tuple[str, ...]) -> tuple[Document, ...]:
        if not document_ids:
            return ()
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.documents "
            "WHERE document_id = ANY(%s) AND ingestion_status = 'complete'",
            (list(document_ids),),
        )
        found = {row["document_id"]: self._document(row) for row in rows}
        return tuple(found[document_id] for document_id in document_ids if document_id in found)

    def find_documents_by_content_hash(
        self, *, namespace: str, content_hash: str
    ) -> tuple[Document, ...]:
        rows = self._fetchall(
            f"""
            SELECT * FROM {SCHEMA}.documents
            WHERE namespace = %s AND content_hash = %s AND ingestion_status = 'complete'
            ORDER BY document_id
            """,
            (namespace, content_hash),
        )
        return tuple(self._document(row) for row in rows)

    def iter_document_ids(
        self, *, namespace: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        after = ""
        while True:
            rows = self._fetchall(
                f"""
                SELECT document_id FROM {SCHEMA}.documents
                WHERE namespace = %s AND ingestion_status = 'complete' AND document_id > %s
                ORDER BY document_id LIMIT %s
                """,
                (namespace, after, batch_size),
            )
            batch = tuple(str(row["document_id"]) for row in rows)
            if not batch:
                break
            yield batch
            after = batch[-1]

    def iter_document_ids_chronological(
        self, *, namespace: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        after_time: datetime | None = None
        after_id = ""
        while True:
            rows = self._fetchall(
                f"""
                WITH ordered AS (
                    SELECT documents.document_id,
                           COALESCE(MIN(atoms.occurred_at), documents.created_at) AS sort_time
                    FROM {SCHEMA}.documents AS documents
                    LEFT JOIN {SCHEMA}.atoms AS atoms USING(document_id)
                    WHERE documents.namespace = %s
                      AND documents.ingestion_status = 'complete'
                    GROUP BY documents.document_id, documents.created_at
                )
                SELECT document_id, sort_time FROM ordered
                WHERE %s::timestamptz IS NULL
                   OR (sort_time, document_id) > (%s::timestamptz, %s)
                ORDER BY sort_time, document_id
                LIMIT %s
                """,
                (namespace, after_time, after_time, after_id, batch_size),
            )
            batch = tuple(str(row["document_id"]) for row in rows)
            if not batch:
                break
            yield batch
            after_time = rows[-1]["sort_time"]
            after_id = batch[-1]

    def get_atoms_for_document(self, document_id: str) -> tuple[Atom, ...]:
        rows = self._fetchall(
            f"""
            SELECT atoms.* FROM {SCHEMA}.atoms AS atoms
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            WHERE atoms.document_id = %s AND documents.ingestion_status = 'complete'
            ORDER BY atoms.position
            """,
            (document_id,),
        )
        return tuple(self._atom(row) for row in rows)

    def iter_atom_ids_for_document(
        self, *, document_id: str, batch_size: int = 1_000
    ) -> Iterator[tuple[str, ...]]:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        after = -1
        while True:
            rows = self._fetchall(
                f"""
                SELECT atoms.atom_id, atoms.position
                FROM {SCHEMA}.atoms AS atoms
                JOIN {SCHEMA}.documents AS documents USING(document_id)
                WHERE atoms.document_id = %s AND atoms.position > %s
                  AND documents.ingestion_status = 'complete'
                ORDER BY atoms.position LIMIT %s
                """,
                (document_id, after, batch_size),
            )
            batch = tuple(str(row["atom_id"]) for row in rows)
            if not batch:
                break
            yield batch
            after = int(rows[-1]["position"])

    def get_document_atom_count(self, document_id: str) -> int:
        row = self._fetchone(
            f"""
            SELECT documents.atom_count
            FROM {SCHEMA}.documents AS documents
            WHERE document_id = %s AND ingestion_status = 'complete'
            """,
            (document_id,),
        )
        return int(row["atom_count"]) if row else 0

    def get_atom(self, atom_id: str) -> Atom | None:
        atoms = self.get_atoms((atom_id,))
        return atoms[0] if atoms else None

    def get_atoms(self, atom_ids: tuple[str, ...]) -> tuple[Atom, ...]:
        if not atom_ids:
            return ()
        rows = self._fetchall(
            f"""
            SELECT atoms.* FROM {SCHEMA}.atoms AS atoms
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            WHERE atoms.atom_id = ANY(%s) AND documents.ingestion_status = 'complete'
            """,
            (list(atom_ids),),
        )
        found = {row["atom_id"]: self._atom(row) for row in rows}
        return tuple(found[atom_id] for atom_id in atom_ids if atom_id in found)

    def find_atoms_by_content_hash(self, *, namespace: str, content_hash: str) -> tuple[Atom, ...]:
        rows = self._fetchall(
            f"""
            SELECT atoms.* FROM {SCHEMA}.atoms AS atoms
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            WHERE atoms.namespace = %s AND atoms.content_hash = %s
              AND documents.ingestion_status = 'complete'
            ORDER BY atoms.document_id, atoms.position
            """,
            (namespace, content_hash),
        )
        return tuple(self._atom(row) for row in rows)

    def get_atom_links(self, atom_id: str) -> tuple[AtomLink, ...]:
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.atom_links WHERE from_atom_id = %s "
            "ORDER BY relation, to_atom_id",
            (atom_id,),
        )
        return tuple(self._atom_link(row) for row in rows)

    def list_atom_links(
        self, *, namespace: str, relation: str | None = None
    ) -> tuple[AtomLink, ...]:
        clauses = ["source_atom.namespace = %s", "documents.ingestion_status = 'complete'"]
        parameters: list[Any] = [namespace]
        if relation is not None:
            clauses.append("links.relation = %s")
            parameters.append(str(relation))
        rows = self._fetchall(
            f"""
            SELECT links.* FROM {SCHEMA}.atom_links AS links
            JOIN {SCHEMA}.atoms AS source_atom ON source_atom.atom_id = links.from_atom_id
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            WHERE {" AND ".join(clauses)}
            ORDER BY links.relation, links.from_atom_id, links.to_atom_id
            """,
            parameters,
        )
        return tuple(self._atom_link(row) for row in rows)

    def get_atom_links_touching(
        self,
        *,
        atom_ids: tuple[str, ...],
        relation: str | None = None,
    ) -> tuple[AtomLink, ...]:
        if not atom_ids:
            return ()
        clauses = ["(from_atom_id = ANY(%s) OR to_atom_id = ANY(%s))"]
        parameters: list[Any] = [list(atom_ids), list(atom_ids)]
        if relation is not None:
            clauses.append("relation = %s")
            parameters.append(str(relation))
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.atom_links WHERE {' AND '.join(clauses)} "
            "ORDER BY relation, from_atom_id, to_atom_id",
            parameters,
        )
        return tuple(self._atom_link(row) for row in rows)

    def atom_tags_for(self, atom_id: str) -> tuple[AtomTag, ...]:
        return self.get_atom_tags_for_atoms((atom_id,))

    def list_atom_tags(self, namespace: str) -> tuple[AtomTag, ...]:
        rows = self._fetchall(
            f"""
            SELECT edges.* FROM {SCHEMA}.atom_tags AS edges
            JOIN {SCHEMA}.atoms AS atoms ON atoms.atom_id = edges.atom_id
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            WHERE atoms.namespace = %s AND documents.ingestion_status = 'complete'
            ORDER BY edges.atom_id, edges.tag_id
            """,
            (namespace,),
        )
        return tuple(self._atom_tag(row) for row in rows)

    def get_atom_tags_for_atoms(self, atom_ids: tuple[str, ...]) -> tuple[AtomTag, ...]:
        if not atom_ids:
            return ()
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.atom_tags WHERE atom_id = ANY(%s) ORDER BY atom_id, tag_id",
            (list(atom_ids),),
        )
        return tuple(self._atom_tag(row) for row in rows)

    def list_atoms(
        self,
        *,
        namespace: str,
        occurred_from: datetime | None = None,
        occurred_to: datetime | None = None,
        kind: AtomKind | None = None,
        role: AtomRole | None = None,
    ) -> tuple[Atom, ...]:
        return tuple(
            atom
            for batch in self.iter_atoms(
                namespace=namespace,
                occurred_from=occurred_from,
                occurred_to=occurred_to,
                kind=kind,
                role=role,
            )
            for atom in batch
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
        clauses = ["atoms.namespace = %s", "documents.ingestion_status = 'complete'"]
        parameters: list[Any] = [namespace]
        if kind is not None:
            clauses.append("atoms.kind = %s")
            parameters.append(kind.value)
        if role is not None:
            clauses.append("atoms.role = %s")
            parameters.append(role.value)
        if occurred_from is not None:
            clauses.append("atoms.occurred_at >= %s")
            parameters.append(occurred_from)
        if occurred_to is not None:
            clauses.append("atoms.occurred_at < %s")
            parameters.append(occurred_to)
        query = f"""
            SELECT atoms.* FROM {SCHEMA}.atoms AS atoms
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            WHERE {" AND ".join(clauses)}
            ORDER BY atoms.document_id, atoms.position
        """
        with self._lock, self._connection.transaction():
            with self._connection.cursor(name="iterate_atoms") as cursor:
                cursor.execute(query, parameters)
                while rows := cursor.fetchmany(batch_size):
                    yield tuple(self._atom(row) for row in rows)

    def list_tags(self, namespace: str, limit: int | None = None) -> tuple[Tag, ...]:
        if limit is not None and limit <= 0:
            return ()
        limit_clause = "" if limit is None else "LIMIT %s"
        parameters: list[Any] = [namespace]
        if limit is not None:
            parameters.append(limit)
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.tags WHERE namespace = %s AND state = 'canonical' "
            f"ORDER BY canonical_text {limit_clause}",
            parameters,
        )
        return tuple(self._tag(row) for row in rows)

    def get_tags(self, tag_ids: tuple[str, ...]) -> tuple[Tag, ...]:
        if not tag_ids:
            return ()
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.tags WHERE state = 'canonical' AND tag_id = ANY(%s)",
            (list(tag_ids),),
        )
        found = {row["tag_id"]: self._tag(row) for row in rows}
        return tuple(found[tag_id] for tag_id in tag_ids if tag_id in found)

    def get_tags_by_canonical(
        self, *, namespace: str, canonical_texts: tuple[str, ...]
    ) -> tuple[Tag, ...]:
        if not canonical_texts:
            return ()
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.tags "
            "WHERE namespace = %s AND state = 'canonical' AND canonical_text = ANY(%s)",
            (namespace, list(canonical_texts)),
        )
        found = {row["canonical_text"]: self._tag(row) for row in rows}
        return tuple(found[value] for value in canonical_texts if value in found)

    def get_tag_candidate(self, candidate_id: str) -> TagCandidate | None:
        row = self._fetchone(
            f"SELECT * FROM {SCHEMA}.tag_candidates WHERE candidate_id = %s",
            (candidate_id,),
        )
        return self._tag_candidate(row) if row else None

    def list_tag_candidates(
        self,
        *,
        namespace: str,
        state: TagCandidateState | None = None,
        limit: int | None = None,
    ) -> tuple[TagCandidate, ...]:
        if limit is not None and limit <= 0:
            return ()
        clauses = ["namespace = %s"]
        parameters: list[Any] = [namespace]
        if state is not None:
            clauses.append("state = %s")
            parameters.append(state.value)
        limit_clause = ""
        if limit is not None:
            limit_clause = "LIMIT %s"
            parameters.append(limit)
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.tag_candidates WHERE {' AND '.join(clauses)} "
            f"ORDER BY created_at, candidate_id {limit_clause}",
            parameters,
        )
        return tuple(self._tag_candidate(row) for row in rows)

    def apply_tag_candidate_resolution(
        self,
        *,
        candidate: TagCandidate,
        tag: Tag | None,
        atom_tag: AtomTag | None,
    ) -> None:
        with self._lock, self._connection.transaction():
            self._lock_weight_namespace(candidate.namespace)
            row = self._connection.execute(
                f"SELECT state FROM {SCHEMA}.tag_candidates WHERE candidate_id = %s FOR UPDATE",
                (candidate.candidate_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown candidate_id: {candidate.candidate_id}")
            if row["state"] != TagCandidateState.PROPOSED.value:
                raise ValueError(f"candidate is already resolved: {candidate.candidate_id}")
            if (tag is None) != (atom_tag is None):
                raise ValueError("tag and atom_tag must be supplied together")
            if tag is not None and tag.state is not TagState.CANONICAL:
                raise ValueError("resolved tag must be canonical")
            if tag is not None and atom_tag is not None:
                events = self._weight_transitions(
                    edges=(atom_tag,),
                    namespace=candidate.namespace,
                    source_type=WeightEventSource.TAG_REVIEW,
                    source_id=candidate.candidate_id,
                    policy_version="tag-review-v1",
                )
                self._upsert_tags((tag,))
                self._upsert_atom_tags((atom_tag,))
                self._upsert_weight_events(events)
            self._upsert_tag_candidates((candidate,))

    def list_tag_relations(
        self, *, namespace: str, relation_type: str | None = None
    ) -> tuple[TagRelation, ...]:
        clauses = ["source_tag.namespace = %s"]
        parameters: list[Any] = [namespace]
        if relation_type is not None:
            clauses.append("relations.relation_type = %s")
            parameters.append(relation_type)
        rows = self._fetchall(
            f"""
            SELECT relations.* FROM {SCHEMA}.tag_relations AS relations
            JOIN {SCHEMA}.tags AS source_tag
              ON source_tag.tag_id = relations.source_tag_id
            WHERE {" AND ".join(clauses)}
            ORDER BY relations.relation_type, relations.source_tag_id,
                     relations.target_tag_id
            """,
            parameters,
        )
        return tuple(self._tag_relation(row) for row in rows)

    def get_tag_relations_touching(
        self,
        *,
        tag_ids: tuple[str, ...],
        relation_type: str | None = None,
    ) -> tuple[TagRelation, ...]:
        if not tag_ids:
            return ()
        clauses = ["(source_tag_id = ANY(%s) OR target_tag_id = ANY(%s))"]
        parameters: list[Any] = [list(tag_ids), list(tag_ids)]
        if relation_type is not None:
            clauses.append("relation_type = %s")
            parameters.append(relation_type)
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.tag_relations WHERE {' AND '.join(clauses)} "
            "ORDER BY relation_type, source_tag_id, target_tag_id",
            parameters,
        )
        return tuple(self._tag_relation(row) for row in rows)

    def search_tag_hits(
        self,
        *,
        namespace: str,
        canonical_tags: tuple[str, ...],
        limit: int,
    ) -> tuple[SearchHit, ...]:
        if not canonical_tags or limit <= 0:
            return ()
        rows = self._fetchall(
            f"""
            SELECT edges.atom_id,
                   SUM(edges.confidence * LN(1 + edges.weight_raw) / LN(2.0))
                       / %s AS score,
                   ARRAY_AGG(tags.canonical_text ORDER BY tags.canonical_text) AS matched_tags
            FROM {SCHEMA}.atom_tags AS edges
            JOIN {SCHEMA}.tags AS tags ON tags.tag_id = edges.tag_id
            JOIN {SCHEMA}.atoms AS atoms ON atoms.atom_id = edges.atom_id
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            WHERE atoms.namespace = %s
              AND documents.ingestion_status = 'complete'
              AND tags.state = 'canonical'
              AND tags.canonical_text = ANY(%s)
            GROUP BY edges.atom_id
            ORDER BY score DESC, edges.atom_id
            LIMIT %s
            """,
            (len(canonical_tags), namespace, list(canonical_tags), limit),
        )
        return tuple(
            SearchHit(
                atom_id=row["atom_id"],
                score=float(row["score"]),
                evidence=tuple(f"tag={value}" for value in row["matched_tags"]),
            )
            for row in rows
        )

    def search_lexical_hits(
        self, *, namespace: str, query: str, limit: int
    ) -> tuple[SearchHit, ...]:
        if not query.strip() or limit <= 0:
            return ()
        rows = self._fetchall(
            f"""
            WITH query AS (SELECT websearch_to_tsquery('simple', %s) AS value)
            SELECT atoms.atom_id, ts_rank_cd(atoms.search_vector, query.value) AS score
            FROM {SCHEMA}.atoms AS atoms
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            CROSS JOIN query
            WHERE atoms.namespace = %s
              AND documents.ingestion_status = 'complete'
              AND atoms.search_vector @@ query.value
            ORDER BY score DESC, atoms.atom_id
            LIMIT %s
            """,
            (query, namespace, limit),
        )
        return tuple(
            SearchHit(row["atom_id"], float(row["score"]), ("lexical=postgres_fts",))
            for row in rows
        )

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
        dimensions = len(query_vector)
        self.ensure_embedding_index(provider=provider, model=model, dimensions=dimensions)
        query = sql.SQL(
            f"""
            SELECT embeddings.atom_id,
                   GREATEST(0.0, 1.0 - (embeddings.embedding::vector({dimensions})
                       <=> %s::vector({dimensions}))) AS score
            FROM {SCHEMA}.atom_embeddings AS embeddings
            JOIN {SCHEMA}.atoms AS atoms ON atoms.atom_id = embeddings.atom_id
            JOIN {SCHEMA}.documents AS documents USING(document_id)
            WHERE atoms.namespace = %s
              AND documents.ingestion_status = 'complete'
              AND embeddings.provider = %s
              AND embeddings.model = %s
              AND embeddings.dimensions = %s
              AND embeddings.content_hash = atoms.content_hash
            ORDER BY embeddings.embedding::vector({dimensions})
                     <=> %s::vector({dimensions})
            LIMIT %s
            """
        )
        # pgvector 0.5 accepts mutable lists or NumPy arrays, while Cortex keeps
        # embeddings immutable in its domain model. Convert only at the adapter boundary.
        vector = Vector(list(query_vector))
        rows = self._fetchall(
            query,
            (vector, namespace, provider, model, dimensions, vector, limit),
        )
        return tuple(
            SearchHit(
                row["atom_id"],
                float(row["score"]),
                (f"semantic={float(row['score']):.4f}",),
            )
            for row in rows
            if float(row["score"]) > 0.0
        )

    def upsert_embeddings(self, embeddings: tuple[AtomEmbedding, ...]) -> None:
        if not embeddings:
            return
        with self._lock, self._connection.transaction():
            self._executemany(
                f"""
                INSERT INTO {SCHEMA}.atom_embeddings (
                    atom_id, provider, model, dimensions, embedding,
                    content_hash, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT(atom_id, provider, model) DO UPDATE SET
                    dimensions = EXCLUDED.dimensions,
                    embedding = EXCLUDED.embedding,
                    content_hash = EXCLUDED.content_hash,
                    created_at = EXCLUDED.created_at
                """,
                (
                    (
                        item.atom_id,
                        item.provider,
                        item.model,
                        item.dimensions,
                        Vector(list(item.vector)),
                        item.content_hash,
                        item.created_at,
                    )
                    for item in embeddings
                ),
            )
        for provider, model, dimensions in {
            (item.provider, item.model, item.dimensions) for item in embeddings
        }:
            self.ensure_embedding_index(provider=provider, model=model, dimensions=dimensions)

    def ensure_embedding_index(self, *, provider: str, model: str, dimensions: int) -> None:
        if dimensions > HNSW_MAX_VECTOR_DIMENSIONS:
            return
        key = (provider, model, dimensions)
        if key in self._indexed_embeddings:
            return
        digest = hashlib.sha256(f"{provider}\0{model}\0{dimensions}".encode()).hexdigest()[:16]
        statement = sql.SQL(
            f"""
            CREATE INDEX IF NOT EXISTS {{index_name}}
            ON {SCHEMA}.atom_embeddings USING hnsw
              ((embedding::vector({dimensions})) vector_cosine_ops)
            WHERE provider = {{provider}} AND model = {{model}}
              AND dimensions = {dimensions}
            """
        ).format(
            index_name=sql.Identifier(f"idx_embedding_hnsw_{digest}"),
            provider=sql.Literal(provider),
            model=sql.Literal(model),
        )
        with self._lock:
            self._connection.execute(statement)
        self._indexed_embeddings.add(key)

    def get_embeddings(
        self,
        *,
        atom_ids: tuple[str, ...],
        provider: str,
        model: str,
    ) -> dict[str, AtomEmbedding]:
        if not atom_ids:
            return {}
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.atom_embeddings "
            "WHERE atom_id = ANY(%s) AND provider = %s AND model = %s",
            (list(atom_ids), provider, model),
        )
        return {row["atom_id"]: self._embedding(row) for row in rows}

    def record_retrieval_event(self, event: dict[str, object]) -> None:
        with self._lock, self._connection.transaction():
            self._connection.execute(
                f"""
                INSERT INTO {SCHEMA}.retrieval_events (
                    retrieval_id, namespace, created_at, payload_json
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    str(event["retrieval_id"]),
                    str(event["namespace"]),
                    datetime.fromisoformat(str(event["created_at"])),
                    Jsonb(self._json_value(event)),
                ),
            )

    def get_retrieval_event(self, retrieval_id: str) -> dict[str, object] | None:
        row = self._fetchone(
            f"SELECT payload_json FROM {SCHEMA}.retrieval_events WHERE retrieval_id = %s",
            (retrieval_id,),
        )
        return dict(row["payload_json"]) if row else None

    def get_calibration_signal_ids(self, signal_ids: tuple[str, ...]) -> frozenset[str]:
        if not signal_ids:
            return frozenset()
        rows = self._fetchall(
            f"SELECT signal_id FROM {SCHEMA}.calibration_signals WHERE signal_id = ANY(%s)",
            (list(signal_ids),),
        )
        return frozenset(str(row["signal_id"]) for row in rows)

    def list_weight_events(
        self,
        *,
        namespace: str,
        target_type: CalibrationTarget | None = None,
        target_id: str | None = None,
        related_id: str | None = None,
        relation_type: str | None = None,
    ) -> tuple[WeightEvent, ...]:
        clauses = ["namespace = %s"]
        parameters: list[Any] = [namespace]
        for column, value in (
            ("target_type", target_type.value if target_type else None),
            ("target_id", target_id),
            ("related_id", related_id),
            ("relation_type", relation_type),
        ):
            if value is not None:
                clauses.append(f"{column} = %s")
                parameters.append(value)
        rows = self._fetchall(
            f"SELECT * FROM {SCHEMA}.weight_events WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at, event_id",
            parameters,
        )
        return tuple(self._weight_event(row) for row in rows)

    def apply_calibration_updates(
        self,
        *,
        signals: tuple[CalibrationSignal, ...],
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None:
        with self._lock, self._connection.transaction():
            namespace = signals[0].namespace if signals else "unknown"
            self._lock_weight_namespace(namespace)
            events = calibration_transition_events(
                edges=(*atom_tags, *atom_links, *tag_relations),
                namespace=namespace,
                previous_weights=self._previous_weight_map(
                    (*atom_tags, *atom_links, *tag_relations)
                ),
                signals=signals,
            )
            self._executemany(
                f"""
                INSERT INTO {SCHEMA}.calibration_signals (
                    signal_id, namespace, target_type, target_id, related_id,
                    relation_type, signal_type, value, confidence, multiplier,
                    provider, profile_version, source_reference, created_at, metadata_json
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    (
                        signal.signal_id,
                        signal.namespace,
                        signal.target_type.value,
                        signal.target_id,
                        signal.related_id,
                        signal.relation_type,
                        signal.signal_type,
                        signal.value,
                        signal.confidence,
                        signal.multiplier,
                        signal.provider,
                        signal.profile_version,
                        signal.source_reference,
                        signal.created_at,
                        Jsonb(self._json_value(signal.metadata)),
                    )
                    for signal in signals
                ),
            )
            self._upsert_atom_tags(atom_tags)
            self._upsert_atom_links(atom_links)
            self._upsert_tag_relations(tag_relations)
            self._upsert_weight_events(events)

    def apply_learning_updates(
        self,
        *,
        feedback_event: dict[str, object],
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None:
        with self._lock, self._connection.transaction():
            self._lock_weight_namespace(str(feedback_event["namespace"]))
            events = self._weight_transitions(
                edges=(*atom_tags, *atom_links, *tag_relations),
                namespace=str(feedback_event["namespace"]),
                source_type=WeightEventSource.FEEDBACK,
                source_id=str(feedback_event["feedback_id"]),
                policy_version=str(feedback_event.get("policy_version", "bounded-feedback-v1")),
                metadata={
                    "retrieval_id": str(feedback_event["retrieval_id"]),
                    "outcome": str(feedback_event["outcome"]),
                },
            )
            self._upsert_atom_tags(atom_tags)
            self._upsert_atom_links(atom_links)
            self._upsert_tag_relations(tag_relations)
            self._upsert_weight_events(events)
            self._connection.execute(
                f"""
                INSERT INTO {SCHEMA}.feedback_events (
                    feedback_id, retrieval_id, created_at, payload_json
                ) VALUES (%s, %s, %s, %s)
                """,
                (
                    str(feedback_event["feedback_id"]),
                    str(feedback_event["retrieval_id"]),
                    datetime.fromisoformat(str(feedback_event["created_at"])),
                    Jsonb(self._json_value(feedback_event)),
                ),
            )

    def restore_weight_aggregates(
        self,
        *,
        atom_tags: tuple[AtomTag, ...],
        atom_links: tuple[AtomLink, ...],
        tag_relations: tuple[TagRelation, ...],
    ) -> None:
        with self._lock, self._connection.transaction():
            self._upsert_atom_tags(atom_tags)
            self._upsert_atom_links(atom_links)
            self._upsert_tag_relations(tag_relations)

    def persist_ingestion(self, bundle: IngestionBundle) -> None:
        with self._lock, self._connection.transaction():
            self._lock_weight_namespace(bundle.document.namespace)
            events = self._weight_transitions(
                edges=(*bundle.atom_tags, *bundle.atom_links),
                namespace=bundle.document.namespace,
                source_type=WeightEventSource.INGESTION,
                source_id=bundle.document.document_id,
                policy_version="canonical-ingestion-v1",
            )
            self._upsert_document(bundle.document, status="complete", atom_count=len(bundle.atoms))
            self._upsert_atoms(bundle.atoms)
            self._upsert_tags(bundle.tags)
            self._upsert_atom_tags(bundle.atom_tags)
            self._upsert_atom_links(bundle.atom_links)
            self._upsert_tag_candidates(bundle.tag_candidates)
            self._upsert_weight_events(events)

    def register_connector_source(self, source: Source) -> Source:
        payload = source_to_mapping(source)
        with self._lock, self._connection.transaction():
            self._connection.execute(
                f"""
                INSERT INTO {SCHEMA}.connector_sources (
                    source_system, source_instance, payload_json
                ) VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                (*source.source.key, Jsonb(payload)),
            )
            row = self._connection.execute(
                f"""
                SELECT payload_json FROM {SCHEMA}.connector_sources
                WHERE source_system = %s AND source_instance = %s
                FOR UPDATE
                """,
                source.source.key,
            ).fetchone()
            assert row is not None
            if row["payload_json"] != payload:
                raise ValueError("connector source registration conflicts with existing source")
        return source

    def get_connector_source(self, source: SourceRef) -> Source | None:
        row = self._fetchone(
            f"SELECT payload_json FROM {SCHEMA}.connector_sources "
            "WHERE source_system = %s AND source_instance = %s",
            source.key,
        )
        return source_from_mapping(row["payload_json"]) if row is not None else None

    def apply_connector_sync_batch(
        self,
        *,
        batch: SyncBatch,
        fingerprint: str,
        acknowledged_at: datetime,
    ) -> SyncBatchAcknowledgement:
        with self._lock, self._connection.transaction():
            source_row = self._connection.execute(
                f"""
                SELECT committed_cursor FROM {SCHEMA}.connector_sources
                WHERE source_system = %s AND source_instance = %s
                FOR UPDATE
                """,
                batch.run.source.key,
            ).fetchone()
            if source_row is None:
                raise ValueError("connector source is not registered")
            run_payload = sync_run_to_mapping(batch.run)
            run_row = self._connection.execute(
                f"SELECT payload_json FROM {SCHEMA}.connector_sync_runs "
                "WHERE request_id = %s FOR UPDATE",
                (batch.run.request_id,),
            ).fetchone()
            if run_row is not None and run_row["payload_json"] != run_payload:
                raise ValueError("sync request identity conflicts with existing run")
            if run_row is None:
                if (
                    batch.run.mode is SyncMode.INCREMENTAL
                    and batch.run.previous_cursor != source_row["committed_cursor"]
                ):
                    raise ValueError("sync previous_cursor does not match committed cursor")
                self._connection.execute(
                    f"""
                    INSERT INTO {SCHEMA}.connector_sync_runs (
                        request_id, source_system, source_instance, payload_json
                    ) VALUES (%s, %s, %s, %s)
                    """,
                    (batch.run.request_id, *batch.run.source.key, Jsonb(run_payload)),
                )
            receipt = self._connection.execute(
                f"""
                SELECT fingerprint, acknowledgement_json
                FROM {SCHEMA}.connector_sync_batches
                WHERE run_request_id = %s AND batch_id = %s AND sequence = %s
                """,
                (batch.run.request_id, batch.batch_id, batch.sequence),
            ).fetchone()
            if receipt is not None:
                if str(receipt["fingerprint"]) != fingerprint:
                    raise ValueError("sync batch identity conflicts with different payload")
                return sync_batch_acknowledgement_from_mapping(
                    receipt["acknowledgement_json"]
                )

            failures: list[SyncItemFailure] = []
            accepted_records = self._persist_connector_records(batch, failures)
            accepted_relations = self._persist_connector_relations(batch, failures)
            accepted_tombstones = self._persist_connector_tombstones(batch, failures)
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
            self._connection.execute(
                f"""
                INSERT INTO {SCHEMA}.connector_sync_batches (
                    run_request_id, batch_id, sequence, fingerprint,
                    acknowledgement_json
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    batch.run.request_id,
                    batch.batch_id,
                    batch.sequence,
                    fingerprint,
                    Jsonb(sync_batch_acknowledgement_to_mapping(acknowledgement)),
                ),
            )
            return acknowledgement

    def _persist_connector_records(
        self, batch: SyncBatch, failures: list[SyncItemFailure]
    ) -> int:
        accepted = 0
        for record in batch.records:
            assert record.ref.external_version is not None
            payload = record_to_mapping(record)
            key = record.ref.version_key
            object_key = record.ref.object_key
            self._connection.execute(
                f"""
                INSERT INTO {SCHEMA}.connector_record_objects (
                    source_system, source_instance, external_id
                ) VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                object_key,
            )
            state = self._connection.execute(
                f"""
                SELECT current_version, current_observed_at
                FROM {SCHEMA}.connector_record_objects
                WHERE source_system = %s AND source_instance = %s
                  AND external_id = %s
                FOR UPDATE
                """,
                object_key,
            ).fetchone()
            assert state is not None
            existing = self._connection.execute(
                f"""
                SELECT payload_json FROM {SCHEMA}.connector_records
                WHERE source_system = %s AND source_instance = %s
                  AND external_id = %s AND external_version = %s
                """,
                key,
            ).fetchone()
            if existing is not None and existing["payload_json"] != payload:
                failures.append(
                    self._connector_failure(
                        SyncItemType.RECORD,
                        record.ref.external_id,
                        record.ref.external_version,
                        "record version already exists with different content",
                    )
                )
                continue
            if existing is None:
                current_observed = state["current_observed_at"]
                becomes_current = (
                    current_observed is None or record.observed_at >= current_observed
                )
                predecessor = (
                    str(state["current_version"])
                    if becomes_current and state["current_version"] is not None
                    else None
                )
                self._connection.execute(
                    f"""
                    INSERT INTO {SCHEMA}.connector_records (
                        source_system, source_instance, external_id, external_version,
                        observed_at, predecessor_version, payload_json
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (*key, record.observed_at, predecessor, Jsonb(payload)),
                )
                if becomes_current:
                    self._connection.execute(
                        f"""
                        UPDATE {SCHEMA}.connector_record_objects
                        SET current_version = %s, current_observed_at = %s
                        WHERE source_system = %s AND source_instance = %s
                          AND external_id = %s
                        """,
                        (
                            record.ref.external_version,
                            record.observed_at,
                            *object_key,
                        ),
                    )
            accepted += 1
        return accepted

    def _persist_connector_relations(
        self, batch: SyncBatch, failures: list[SyncItemFailure]
    ) -> int:
        accepted = 0
        for relation in batch.relations:
            payload = relation_to_mapping(relation)
            key = relation.version_key
            existing = self._connection.execute(
                f"""
                SELECT payload_json FROM {SCHEMA}.connector_relations
                WHERE source_system = %s AND source_instance = %s
                  AND relation_id = %s AND relation_version = %s
                """,
                key,
            ).fetchone()
            if existing is not None and existing["payload_json"] != payload:
                failures.append(
                    self._connector_failure(
                        SyncItemType.RELATION,
                        relation.relation_id,
                        relation.relation_version,
                        "relation version already exists with different content",
                    )
                )
                continue
            if existing is None and not all(
                self._connector_record_exists(endpoint)
                for endpoint in (relation.source, relation.target)
            ):
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
                self._connection.execute(
                    f"""
                    INSERT INTO {SCHEMA}.connector_relations (
                        source_system, source_instance, relation_id,
                        relation_version, payload_json
                    ) VALUES (%s, %s, %s, %s, %s)
                    """,
                    (*key, Jsonb(payload)),
                )
            accepted += 1
        return accepted

    def _persist_connector_tombstones(
        self, batch: SyncBatch, failures: list[SyncItemFailure]
    ) -> int:
        accepted = 0
        for tombstone in batch.tombstones:
            payload = tombstone_to_mapping(tombstone)
            key = tombstone.version_key
            object_key = tombstone.record.object_key
            self._connection.execute(
                f"""
                INSERT INTO {SCHEMA}.connector_record_objects (
                    source_system, source_instance, external_id
                ) VALUES (%s, %s, %s)
                ON CONFLICT DO NOTHING
                """,
                object_key,
            )
            state = self._connection.execute(
                f"""
                SELECT tombstoned_at FROM {SCHEMA}.connector_record_objects
                WHERE source_system = %s AND source_instance = %s
                  AND external_id = %s
                FOR UPDATE
                """,
                object_key,
            ).fetchone()
            assert state is not None
            existing = self._connection.execute(
                f"""
                SELECT payload_json FROM {SCHEMA}.connector_tombstones
                WHERE source_system = %s AND source_instance = %s
                  AND external_id = %s AND tombstone_version = %s
                """,
                key,
            ).fetchone()
            if existing is not None and existing["payload_json"] != payload:
                failures.append(
                    self._connector_failure(
                        SyncItemType.TOMBSTONE,
                        tombstone.record.external_id,
                        tombstone.tombstone_version,
                        "tombstone version already exists with different content",
                    )
                )
                continue
            if existing is None:
                self._connection.execute(
                    f"""
                    INSERT INTO {SCHEMA}.connector_tombstones (
                        source_system, source_instance, external_id,
                        tombstone_version, observed_at, payload_json
                    ) VALUES (%s, %s, %s, %s, %s, %s)
                    """,
                    (*key, tombstone.observed_at, Jsonb(payload)),
                )
                if (
                    state["tombstoned_at"] is None
                    or tombstone.observed_at >= state["tombstoned_at"]
                ):
                    self._connection.execute(
                        f"""
                        UPDATE {SCHEMA}.connector_record_objects
                        SET tombstone_version = %s, tombstoned_at = %s
                        WHERE source_system = %s AND source_instance = %s
                          AND external_id = %s
                        """,
                        (
                            tombstone.tombstone_version,
                            tombstone.observed_at,
                            *object_key,
                        ),
                    )
            accepted += 1
        return accepted

    def commit_connector_sync(
        self,
        *,
        request_id: str,
        run_request_id: str,
        committed_at: datetime,
    ) -> SyncCommitAcknowledgement:
        with self._lock, self._connection.transaction():
            existing = self._connection.execute(
                f"""
                SELECT request_id, commit_ack_json
                FROM {SCHEMA}.connector_sync_runs
                WHERE commit_request_id = %s
                """,
                (request_id,),
            ).fetchone()
            if existing is not None:
                if str(existing["request_id"]) != run_request_id:
                    raise ValueError(
                        "sync commit request identity conflicts with existing commit"
                    )
                return sync_commit_acknowledgement_from_mapping(
                    existing["commit_ack_json"]
                )
            run_row = self._connection.execute(
                f"""
                SELECT payload_json, status FROM {SCHEMA}.connector_sync_runs
                WHERE request_id = %s FOR UPDATE
                """,
                (run_request_id,),
            ).fetchone()
            if run_row is None:
                raise ValueError("sync run does not exist")
            if str(run_row["status"]) == "committed":
                raise ValueError("sync run was already committed by a different request")
            receipts = self._connection.execute(
                f"""
                SELECT acknowledgement_json FROM {SCHEMA}.connector_sync_batches
                WHERE run_request_id = %s
                """,
                (run_request_id,),
            ).fetchall()
            if not receipts:
                raise ValueError("sync run has no durable batches")
            acknowledgements = tuple(
                sync_batch_acknowledgement_from_mapping(row["acknowledgement_json"])
                for row in receipts
            )
            unresolved_failures = tuple(
                failure
                for acknowledgement in acknowledgements
                for failure in acknowledgement.failures
                if not self._connector_failure_is_resolved(run_request_id, failure)
            )
            if unresolved_failures:
                raise ValueError("sync run has item failures and cannot commit its cursor")
            run = sync_run_from_mapping(run_row["payload_json"])
            if run.proposed_cursor is None:
                raise ValueError("sync run requires proposed_cursor before commit")
            source_row = self._connection.execute(
                f"""
                SELECT committed_cursor FROM {SCHEMA}.connector_sources
                WHERE source_system = %s AND source_instance = %s
                FOR UPDATE
                """,
                run.source.key,
            ).fetchone()
            assert source_row is not None
            if (
                run.mode is SyncMode.INCREMENTAL
                and run.previous_cursor != source_row["committed_cursor"]
            ):
                raise ValueError("sync previous_cursor no longer matches committed cursor")
            acknowledgement = SyncCommitAcknowledgement(
                request_id=request_id,
                run_request_id=run_request_id,
                source=run.source,
                committed_cursor=run.proposed_cursor,
                committed_at=committed_at,
            )
            self._connection.execute(
                f"""
                UPDATE {SCHEMA}.connector_sources SET committed_cursor = %s
                WHERE source_system = %s AND source_instance = %s
                """,
                (run.proposed_cursor, *run.source.key),
            )
            self._connection.execute(
                f"""
                UPDATE {SCHEMA}.connector_sync_runs
                SET status = 'committed', commit_request_id = %s, commit_ack_json = %s
                WHERE request_id = %s
                """,
                (
                    request_id,
                    Jsonb(sync_commit_acknowledgement_to_mapping(acknowledgement)),
                    run_request_id,
                ),
            )
            return acknowledgement

    def _connector_failure_is_resolved(
        self, run_request_id: str, failure: SyncItemFailure
    ) -> bool:
        if not failure.retryable or failure.item_type is not SyncItemType.RELATION:
            return False
        row = self._connection.execute(
            f"""
            SELECT 1
            FROM {SCHEMA}.connector_relations AS relations
            JOIN {SCHEMA}.connector_sync_runs AS runs
              ON runs.source_system = relations.source_system
             AND runs.source_instance = relations.source_instance
            WHERE runs.request_id = %s AND relations.relation_id = %s
              AND relations.relation_version = %s
            """,
            (run_request_id, failure.item_id, failure.item_version),
        ).fetchone()
        return row is not None

    def get_connector_sync_run(self, request_id: str) -> SyncRun | None:
        row = self._fetchone(
            f"SELECT payload_json FROM {SCHEMA}.connector_sync_runs WHERE request_id = %s",
            (request_id,),
        )
        return sync_run_from_mapping(row["payload_json"]) if row is not None else None

    def get_connector_record(self, record: RecordRef) -> Record | None:
        if record.external_version is None:
            raise ValueError("connector record lookup requires external_version")
        row = self._fetchone(
            f"""
            SELECT payload_json FROM {SCHEMA}.connector_records
            WHERE source_system = %s AND source_instance = %s
              AND external_id = %s AND external_version = %s
            """,
            record.version_key,
        )
        return (
            record_from_mapping(row["payload_json"], record.source)
            if row is not None
            else None
        )

    def get_current_connector_record(
        self, *, source: SourceRef, external_id: str
    ) -> Record | None:
        row = self._fetchone(
            f"""
            SELECT records.payload_json, objects.current_observed_at,
                   objects.tombstoned_at
            FROM {SCHEMA}.connector_record_objects AS objects
            JOIN {SCHEMA}.connector_records AS records
              ON records.source_system = objects.source_system
             AND records.source_instance = objects.source_instance
             AND records.external_id = objects.external_id
             AND records.external_version = objects.current_version
            WHERE objects.source_system = %s AND objects.source_instance = %s
              AND objects.external_id = %s
            """,
            (*source.key, external_id),
        )
        if row is None:
            return None
        if (
            row["tombstoned_at"] is not None
            and row["tombstoned_at"] >= row["current_observed_at"]
        ):
            return None
        return record_from_mapping(row["payload_json"], source)

    def get_connector_record_predecessor(self, record: RecordRef) -> RecordRef | None:
        if record.external_version is None:
            raise ValueError("connector predecessor lookup requires external_version")
        row = self._fetchone(
            f"""
            SELECT predecessor_version FROM {SCHEMA}.connector_records
            WHERE source_system = %s AND source_instance = %s
              AND external_id = %s AND external_version = %s
            """,
            record.version_key,
        )
        if row is None or row["predecessor_version"] is None:
            return None
        return RecordRef(
            source=record.source,
            external_id=record.external_id,
            external_version=str(row["predecessor_version"]),
        )

    def get_connector_relation(
        self, *, source: SourceRef, relation_id: str, relation_version: str
    ) -> Relation | None:
        row = self._fetchone(
            f"""
            SELECT payload_json FROM {SCHEMA}.connector_relations
            WHERE source_system = %s AND source_instance = %s
              AND relation_id = %s AND relation_version = %s
            """,
            (*source.key, relation_id, relation_version),
        )
        return (
            relation_from_mapping(row["payload_json"], source) if row is not None else None
        )

    def get_connector_cursor(self, source: SourceRef) -> str | None:
        row = self._fetchone(
            f"""
            SELECT committed_cursor FROM {SCHEMA}.connector_sources
            WHERE source_system = %s AND source_instance = %s
            """,
            source.key,
        )
        return str(row["committed_cursor"]) if row and row["committed_cursor"] else None

    def _connector_record_exists(self, record: RecordRef) -> bool:
        if record.external_version is None:
            return False
        row = self._connection.execute(
            f"""
            SELECT 1 FROM {SCHEMA}.connector_records
            WHERE source_system = %s AND source_instance = %s
              AND external_id = %s AND external_version = %s
            """,
            record.version_key,
        ).fetchone()
        return row is not None

    @staticmethod
    def _connector_failure(
        item_type: SyncItemType,
        item_id: str,
        item_version: str,
        message: str,
    ) -> SyncItemFailure:
        return SyncItemFailure(
            item_type=item_type,
            item_id=item_id,
            item_version=item_version,
            code="version_conflict",
            message=message,
            retryable=False,
        )

    def begin_staged_ingestion(self, *, document: Document, tags: tuple[Tag, ...]) -> bool:
        row = self._fetchone(
            f"SELECT ingestion_status FROM {SCHEMA}.documents WHERE document_id = %s",
            (document.document_id,),
        )
        if row and row["ingestion_status"] == "complete":
            return False
        with self._lock, self._connection.transaction():
            self._upsert_document(document, status="staging", atom_count=0)
            self._upsert_tags(tags)
        return True

    def append_staged_ingestion(
        self, *, atoms: tuple[Atom, ...], atom_tags: tuple[AtomTag, ...]
    ) -> None:
        with self._lock, self._connection.transaction():
            namespace = atoms[0].namespace if atoms else "unknown"
            self._lock_weight_namespace(namespace)
            events = self._weight_transitions(
                edges=atom_tags,
                namespace=namespace,
                source_type=WeightEventSource.INGESTION,
                source_id=atoms[0].document_id if atoms else "empty-stage",
                policy_version="canonical-ingestion-v1",
            )
            self._upsert_atoms(atoms)
            self._upsert_atom_tags(atom_tags)
            self._upsert_weight_events(events)

    def complete_staged_ingestion(self, *, document_id: str, atom_count: int) -> None:
        with self._lock, self._connection.transaction():
            row = self._connection.execute(
                f"SELECT COUNT(*) AS count FROM {SCHEMA}.atoms WHERE document_id = %s",
                (document_id,),
            ).fetchone()
            persisted = int(row["count"])
            if persisted != atom_count:
                raise ValueError(
                    f"staged atom count mismatch: expected {atom_count}, found {persisted}"
                )
            result = self._connection.execute(
                f"UPDATE {SCHEMA}.documents SET ingestion_status = 'complete', "
                "atom_count = %s WHERE document_id = %s AND ingestion_status = 'staging'",
                (atom_count, document_id),
            )
            if result.rowcount != 1:
                raise ValueError(f"unknown staging document: {document_id}")

    def close(self) -> None:
        with self._lock:
            self._connection.close()

    def __enter__(self) -> PostgreSQLRepository:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    @property
    def document_count(self) -> int:
        return self._count("documents", "ingestion_status = 'complete'")

    @property
    def atom_count(self) -> int:
        return self._count(
            "atoms",
            f"document_id IN (SELECT document_id FROM {SCHEMA}.documents "
            "WHERE ingestion_status = 'complete')",
        )

    @property
    def tag_count(self) -> int:
        return self._count("tags")

    @property
    def atom_tag_count(self) -> int:
        return self._count("atom_tags")

    @property
    def atom_link_count(self) -> int:
        return self._count("atom_links")

    def _count(self, table: str, where: str = "TRUE") -> int:
        if table not in {"documents", "atoms", "tags", "atom_tags", "atom_links"}:
            raise ValueError(f"unsupported table: {table}")
        row = self._fetchone(f"SELECT COUNT(*) AS count FROM {SCHEMA}.{table} WHERE {where}")
        return int(row["count"])

    def _upsert_document(self, document: Document, *, status: str, atom_count: int) -> None:
        self._connection.execute(
            f"""
            INSERT INTO {SCHEMA}.documents (
                document_id, namespace, source, content_hash, created_at,
                metadata_json, ingestion_status, atom_count
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(document_id) DO UPDATE SET
                namespace = EXCLUDED.namespace,
                source = EXCLUDED.source,
                content_hash = EXCLUDED.content_hash,
                metadata_json = EXCLUDED.metadata_json,
                ingestion_status = EXCLUDED.ingestion_status,
                atom_count = EXCLUDED.atom_count
            """,
            (
                document.document_id,
                document.namespace,
                document.source,
                document.content_hash,
                document.created_at,
                Jsonb(self._json_value(document.metadata)),
                status,
                atom_count,
            ),
        )

    def _upsert_atoms(self, atoms: Iterable[Atom]) -> None:
        self._executemany(
            f"""
            INSERT INTO {SCHEMA}.atoms (
                atom_id, document_id, namespace, position, char_start, char_end,
                content, content_hash, kind, role, modality, occurred_at, created_at,
                metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(atom_id) DO UPDATE SET
                document_id = EXCLUDED.document_id,
                namespace = EXCLUDED.namespace,
                position = EXCLUDED.position,
                char_start = EXCLUDED.char_start,
                char_end = EXCLUDED.char_end,
                content = EXCLUDED.content,
                content_hash = EXCLUDED.content_hash,
                kind = EXCLUDED.kind,
                role = EXCLUDED.role,
                modality = EXCLUDED.modality,
                occurred_at = EXCLUDED.occurred_at,
                metadata_json = EXCLUDED.metadata_json
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
                    atom.kind.value,
                    (atom.role or AtomRole.SOURCE).value,
                    atom.modality.value,
                    atom.occurred_at,
                    atom.created_at,
                    Jsonb(self._json_value(atom.metadata)),
                )
                for atom in atoms
            ),
        )

    def _upsert_tags(self, tags: Iterable[Tag]) -> None:
        self._executemany(
            f"""
            INSERT INTO {SCHEMA}.tags (
                tag_id, namespace, canonical_text, display_text, level, state,
                aliases, created_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(tag_id) DO UPDATE SET
                namespace = EXCLUDED.namespace,
                canonical_text = EXCLUDED.canonical_text,
                display_text = EXCLUDED.display_text,
                level = EXCLUDED.level,
                state = EXCLUDED.state,
                aliases = EXCLUDED.aliases
            """,
            (
                (
                    tag.tag_id,
                    tag.namespace,
                    tag.canonical_text,
                    tag.display_text,
                    tag.level.value,
                    tag.state.value,
                    list(tag.aliases),
                    tag.created_at,
                )
                for tag in tags
            ),
        )

    def _upsert_atom_tags(self, atom_tags: Iterable[AtomTag]) -> None:
        self._executemany(
            f"""
            INSERT INTO {SCHEMA}.atom_tags (
                atom_id, tag_id, weight_raw, confidence, origin,
                evidence_sources, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(atom_id, tag_id) DO UPDATE SET
                weight_raw = EXCLUDED.weight_raw,
                confidence = EXCLUDED.confidence,
                origin = EXCLUDED.origin,
                evidence_sources = EXCLUDED.evidence_sources,
                updated_at = EXCLUDED.updated_at
            """,
            (
                (
                    edge.atom_id,
                    edge.tag_id,
                    edge.weight_raw,
                    edge.confidence,
                    edge.origin.value,
                    list(edge.evidence_sources),
                    edge.created_at,
                    edge.updated_at,
                )
                for edge in atom_tags
            ),
        )

    def _upsert_tag_candidates(self, candidates: Iterable[TagCandidate]) -> None:
        self._executemany(
            f"""
            INSERT INTO {SCHEMA}.tag_candidates (
                candidate_id, namespace, atom_id, normalized_text, display_text,
                level, confidence, state, producer, proposal_version,
                resolved_tag_id, resolution_reason, created_at, resolved_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(candidate_id) DO UPDATE SET
                state = EXCLUDED.state,
                resolved_tag_id = EXCLUDED.resolved_tag_id,
                resolution_reason = EXCLUDED.resolution_reason,
                resolved_at = EXCLUDED.resolved_at
            """,
            (
                (
                    candidate.candidate_id,
                    candidate.namespace,
                    candidate.atom_id,
                    candidate.normalized_text,
                    candidate.display_text,
                    candidate.level.value,
                    candidate.confidence,
                    candidate.state.value,
                    candidate.producer,
                    candidate.proposal_version,
                    candidate.resolved_tag_id,
                    candidate.resolution_reason,
                    candidate.created_at,
                    candidate.resolved_at,
                )
                for candidate in candidates
            ),
        )

    def _upsert_weight_events(self, events: Iterable[WeightEvent]) -> None:
        self._executemany(
            f"""
            INSERT INTO {SCHEMA}.weight_events (
                event_id, namespace, target_type, target_id, related_id,
                relation_type, source_type, source_id, policy_version,
                weight_before, weight_after, delta, created_at, metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(event_id) DO NOTHING
            """,
            (
                (
                    event.event_id,
                    event.namespace,
                    event.target_type.value,
                    event.target_id,
                    event.related_id,
                    event.relation_type,
                    event.source_type.value,
                    event.source_id,
                    event.policy_version,
                    event.weight_before,
                    event.weight_after,
                    event.delta,
                    event.created_at,
                    Jsonb(self._json_value(event.metadata)),
                )
                for event in events
            ),
        )

    def _weight_transitions(
        self,
        *,
        edges: tuple[AtomTag | AtomLink | TagRelation, ...],
        namespace: str,
        source_type: WeightEventSource,
        source_id: str,
        policy_version: str,
        metadata: dict[str, object] | None = None,
    ) -> tuple[WeightEvent, ...]:
        return transition_events(
            namespace=namespace,
            edges=edges,
            previous_weights=self._previous_weight_map(edges),
            source_type=source_type,
            source_id=source_id,
            policy_version=policy_version,
            metadata=metadata,
        )

    def _previous_weight_map(
        self, edges: tuple[AtomTag | AtomLink | TagRelation, ...]
    ) -> dict[EdgeCoordinates, float]:
        atom_ids = tuple(
            sorted(
                {
                    value
                    for edge in edges
                    if isinstance(edge, AtomTag | AtomLink)
                    for value in (
                        (edge.atom_id,)
                        if isinstance(edge, AtomTag)
                        else (
                            edge.from_atom_id,
                            edge.to_atom_id,
                        )
                    )
                }
            )
        )
        tag_ids = tuple(
            sorted(
                {
                    value
                    for edge in edges
                    if isinstance(edge, TagRelation)
                    for value in (edge.source_tag_id, edge.target_tag_id)
                }
            )
        )
        previous_edges = (
            *self.get_atom_tags_for_atoms(atom_ids),
            *self.get_atom_links_touching(atom_ids=atom_ids),
            *self.get_tag_relations_touching(tag_ids=tag_ids),
        )
        previous_weights = {edge_coordinates(edge): edge.weight_raw for edge in previous_edges}
        return previous_weights

    def _upsert_atom_links(self, atom_links: Iterable[AtomLink]) -> None:
        self._executemany(
            f"""
            INSERT INTO {SCHEMA}.atom_links (
                from_atom_id, to_atom_id, relation, weight_raw, confidence,
                evidence_sources, created_at, updated_at, metadata_json
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(from_atom_id, to_atom_id, relation) DO UPDATE SET
                weight_raw = EXCLUDED.weight_raw,
                confidence = EXCLUDED.confidence,
                evidence_sources = EXCLUDED.evidence_sources,
                updated_at = EXCLUDED.updated_at,
                metadata_json = EXCLUDED.metadata_json
            """,
            (
                (
                    edge.from_atom_id,
                    edge.to_atom_id,
                    edge.relation.value,
                    edge.weight_raw,
                    edge.confidence,
                    list(edge.evidence_sources),
                    edge.created_at,
                    edge.updated_at,
                    Jsonb(self._json_value(edge.metadata)),
                )
                for edge in atom_links
            ),
        )

    def _upsert_tag_relations(self, relations: Iterable[TagRelation]) -> None:
        self._executemany(
            f"""
            INSERT INTO {SCHEMA}.tag_relations (
                source_tag_id, target_tag_id, relation_type, weight_raw,
                confidence, evidence_sources, created_at, updated_at
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            ON CONFLICT(source_tag_id, target_tag_id, relation_type) DO UPDATE SET
                weight_raw = EXCLUDED.weight_raw,
                confidence = EXCLUDED.confidence,
                evidence_sources = EXCLUDED.evidence_sources,
                updated_at = EXCLUDED.updated_at
            """,
            (
                (
                    edge.source_tag_id,
                    edge.target_tag_id,
                    edge.relation_type,
                    edge.weight_raw,
                    edge.confidence,
                    list(edge.evidence_sources),
                    edge.created_at,
                    edge.updated_at,
                )
                for edge in relations
            ),
        )

    def _migrate_legacy_proposed_tags(self) -> None:
        """Quarantine pre-lifecycle proposed tags without losing their evidence."""
        self._connection.execute(
            f"""
            UPDATE {SCHEMA}.tags AS tags
            SET state = 'canonical'
            WHERE tags.state = 'proposed_new'
              AND EXISTS (
                  SELECT 1 FROM {SCHEMA}.atom_tags AS edges
                  WHERE edges.tag_id = tags.tag_id
                    AND 'explicit' = ANY(edges.evidence_sources)
              )
            """
        )
        self._connection.execute(
            f"""
            UPDATE {SCHEMA}.atom_tags AS edges
            SET origin = 'explicit'
            FROM {SCHEMA}.tags AS tags
            WHERE edges.tag_id = tags.tag_id
              AND tags.state = 'canonical'
              AND 'explicit' = ANY(edges.evidence_sources)
            """
        )
        self._connection.execute(
            f"""
            INSERT INTO {SCHEMA}.tag_candidates (
                candidate_id, namespace, atom_id, normalized_text, display_text,
                level, confidence, state, producer, proposal_version,
                resolved_tag_id, resolution_reason, created_at, resolved_at
            )
            SELECT
                'candidate_legacy_' || edges.atom_id || '_' || tags.tag_id,
                tags.namespace,
                edges.atom_id,
                tags.canonical_text,
                tags.display_text,
                tags.level,
                edges.confidence,
                'proposed',
                COALESCE(edges.evidence_sources[1], 'legacy-unknown'),
                'pre-tag-candidates-v1',
                NULL,
                'migrated from proposed_new tag and quarantined; evidence=' ||
                    array_to_string(edges.evidence_sources, ','),
                edges.created_at,
                NULL
            FROM {SCHEMA}.atom_tags AS edges
            JOIN {SCHEMA}.tags AS tags ON tags.tag_id = edges.tag_id
            WHERE tags.state = 'proposed_new'
            ON CONFLICT(candidate_id) DO NOTHING
            """
        )
        self._connection.execute(
            f"DELETE FROM {SCHEMA}.atom_tags AS edges USING {SCHEMA}.tags AS tags "
            "WHERE edges.tag_id = tags.tag_id AND tags.state = 'proposed_new'"
        )
        self._connection.execute(
            f"DELETE FROM {SCHEMA}.tag_relations AS relations "
            f"USING {SCHEMA}.tags AS tags "
            "WHERE tags.state = 'proposed_new' AND "
            "(relations.source_tag_id = tags.tag_id OR relations.target_tag_id = tags.tag_id)"
        )
        self._connection.execute(f"DELETE FROM {SCHEMA}.tags WHERE state = 'proposed_new'")

    def _seed_weight_event_baselines(self) -> None:
        """Create honest starting snapshots for edges written before the ledger existed."""
        metadata = Jsonb({"historical_detail_unavailable": True})
        self._connection.execute(
            f"""
            INSERT INTO {SCHEMA}.weight_events (
                event_id, namespace, target_type, target_id, related_id,
                relation_type, source_type, source_id, policy_version,
                weight_before, weight_after, delta, created_at, metadata_json
            )
            SELECT
                'weight_migration_atom_tag_' || edges.atom_id || '_' || edges.tag_id,
                atoms.namespace,
                'atom_tag', edges.atom_id, edges.tag_id, 'has_tag',
                'migration', 'pre-ledger-current-state', 'migration-baseline-v1',
                0.0, edges.weight_raw, edges.weight_raw, edges.updated_at, %s
            FROM {SCHEMA}.atom_tags AS edges
            JOIN {SCHEMA}.atoms AS atoms ON atoms.atom_id = edges.atom_id
            WHERE NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.weight_events AS events
                WHERE events.target_type = 'atom_tag'
                  AND events.target_id = edges.atom_id
                  AND events.related_id = edges.tag_id
                  AND events.relation_type = 'has_tag'
            )
            ON CONFLICT(event_id) DO NOTHING
            """,
            (metadata,),
        )
        self._connection.execute(
            f"""
            INSERT INTO {SCHEMA}.weight_events (
                event_id, namespace, target_type, target_id, related_id,
                relation_type, source_type, source_id, policy_version,
                weight_before, weight_after, delta, created_at, metadata_json
            )
            SELECT
                'weight_migration_atom_link_' || links.from_atom_id || '_' ||
                    links.to_atom_id || '_' || links.relation,
                atoms.namespace,
                'atom_link', links.from_atom_id, links.to_atom_id, links.relation,
                'migration', 'pre-ledger-current-state', 'migration-baseline-v1',
                0.0, links.weight_raw, links.weight_raw, links.updated_at, %s
            FROM {SCHEMA}.atom_links AS links
            JOIN {SCHEMA}.atoms AS atoms ON atoms.atom_id = links.from_atom_id
            WHERE NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.weight_events AS events
                WHERE events.target_type = 'atom_link'
                  AND events.target_id = links.from_atom_id
                  AND events.related_id = links.to_atom_id
                  AND events.relation_type = links.relation
            )
            ON CONFLICT(event_id) DO NOTHING
            """,
            (metadata,),
        )
        self._connection.execute(
            f"""
            INSERT INTO {SCHEMA}.weight_events (
                event_id, namespace, target_type, target_id, related_id,
                relation_type, source_type, source_id, policy_version,
                weight_before, weight_after, delta, created_at, metadata_json
            )
            SELECT
                'weight_migration_tag_relation_' || relations.source_tag_id || '_' ||
                    relations.target_tag_id || '_' || relations.relation_type,
                tags.namespace,
                'tag_relation', relations.source_tag_id, relations.target_tag_id,
                    relations.relation_type,
                'migration', 'pre-ledger-current-state', 'migration-baseline-v1',
                0.0, relations.weight_raw, relations.weight_raw, relations.updated_at, %s
            FROM {SCHEMA}.tag_relations AS relations
            JOIN {SCHEMA}.tags AS tags ON tags.tag_id = relations.source_tag_id
            WHERE NOT EXISTS (
                SELECT 1 FROM {SCHEMA}.weight_events AS events
                WHERE events.target_type = 'tag_relation'
                  AND events.target_id = relations.source_tag_id
                  AND events.related_id = relations.target_tag_id
                  AND events.relation_type = relations.relation_type
            )
            ON CONFLICT(event_id) DO NOTHING
            """,
            (metadata,),
        )

    def _fetchone(self, query, parameters: Iterable[Any] = ()):
        with self._lock:
            return self._connection.execute(query, parameters).fetchone()

    def _lock_weight_namespace(self, namespace: str) -> None:
        digest = hashlib.sha256(f"weight-ledger\0{namespace}".encode()).digest()[:8]
        lock_key = int.from_bytes(digest, byteorder="big", signed=True)
        self._connection.execute("SELECT pg_advisory_xact_lock(%s)", (lock_key,))

    def _fetchall(self, query, parameters: Iterable[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._connection.execute(query, parameters).fetchall())

    def _executemany(self, query, parameters: Iterable[Iterable[Any]]) -> None:
        with self._connection.cursor() as cursor:
            cursor.executemany(query, parameters)

    @staticmethod
    def _document(row: dict[str, Any]) -> Document:
        return Document(
            document_id=row["document_id"],
            namespace=row["namespace"],
            source=row["source"],
            content_hash=row["content_hash"],
            created_at=row["created_at"],
            metadata=dict(row["metadata_json"]),
        )

    @staticmethod
    def _atom(row: dict[str, Any]) -> Atom:
        return Atom(
            atom_id=row["atom_id"],
            document_id=row["document_id"],
            namespace=row["namespace"],
            position=int(row["position"]),
            char_start=int(row["char_start"]),
            char_end=int(row["char_end"]),
            content=row["content"],
            content_hash=row["content_hash"],
            kind=AtomKind(row["kind"]),
            role=AtomRole(row["role"]),
            modality=PayloadModality(row["modality"]),
            occurred_at=row["occurred_at"],
            created_at=row["created_at"],
            metadata=dict(row["metadata_json"]),
        )

    @staticmethod
    def _tag(row: dict[str, Any]) -> Tag:
        return Tag(
            tag_id=row["tag_id"],
            namespace=row["namespace"],
            canonical_text=row["canonical_text"],
            display_text=row["display_text"],
            level=TagLevel(row["level"]),
            state=TagState(row["state"]),
            aliases=tuple(row["aliases"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _tag_candidate(row: dict[str, Any]) -> TagCandidate:
        return TagCandidate(
            candidate_id=row["candidate_id"],
            namespace=row["namespace"],
            atom_id=row["atom_id"],
            normalized_text=row["normalized_text"],
            display_text=row["display_text"],
            level=TagLevel(row["level"]),
            confidence=float(row["confidence"]),
            state=TagCandidateState(row["state"]),
            producer=row["producer"],
            proposal_version=row["proposal_version"],
            resolved_tag_id=row["resolved_tag_id"],
            resolution_reason=row["resolution_reason"],
            created_at=row["created_at"],
            resolved_at=row["resolved_at"],
        )

    @staticmethod
    def _weight_event(row: dict[str, Any]) -> WeightEvent:
        return WeightEvent(
            event_id=row["event_id"],
            namespace=row["namespace"],
            target_type=CalibrationTarget(row["target_type"]),
            target_id=row["target_id"],
            related_id=row["related_id"],
            relation_type=row["relation_type"],
            source_type=WeightEventSource(row["source_type"]),
            source_id=row["source_id"],
            policy_version=row["policy_version"],
            weight_before=float(row["weight_before"]),
            weight_after=float(row["weight_after"]),
            delta=float(row["delta"]),
            created_at=row["created_at"],
            metadata=dict(row["metadata_json"]),
        )

    @staticmethod
    def _atom_tag(row: dict[str, Any]) -> AtomTag:
        return AtomTag(
            atom_id=row["atom_id"],
            tag_id=row["tag_id"],
            weight_raw=float(row["weight_raw"]),
            confidence=float(row["confidence"]),
            origin=TagOrigin(row["origin"]),
            evidence_sources=tuple(row["evidence_sources"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _atom_link(row: dict[str, Any]) -> AtomLink:
        return AtomLink(
            from_atom_id=row["from_atom_id"],
            to_atom_id=row["to_atom_id"],
            relation=AtomLinkRelation(row["relation"]),
            weight_raw=float(row["weight_raw"]),
            confidence=float(row["confidence"]),
            evidence_sources=tuple(row["evidence_sources"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=dict(row["metadata_json"]),
        )

    @staticmethod
    def _tag_relation(row: dict[str, Any]) -> TagRelation:
        return TagRelation(
            source_tag_id=row["source_tag_id"],
            target_tag_id=row["target_tag_id"],
            relation_type=row["relation_type"],
            weight_raw=float(row["weight_raw"]),
            confidence=float(row["confidence"]),
            evidence_sources=tuple(row["evidence_sources"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    @staticmethod
    def _embedding(row: dict[str, Any]) -> AtomEmbedding:
        return AtomEmbedding(
            atom_id=row["atom_id"],
            provider=row["provider"],
            model=row["model"],
            dimensions=int(row["dimensions"]),
            vector=tuple(float(value) for value in row["embedding"]),
            content_hash=row["content_hash"],
            created_at=row["created_at"],
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        return json.loads(json.dumps(value, sort_keys=True, default=str))

    @staticmethod
    def _escape_like(value: str) -> str:
        return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")

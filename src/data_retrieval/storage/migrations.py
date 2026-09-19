from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class MigrationIdentity:
    """One logical schema change shared by every persistent adapter."""

    version: int
    name: str

    def __post_init__(self) -> None:
        if self.version <= 0:
            raise ValueError("migration version must be positive")
        if not self.name.strip():
            raise ValueError("migration name cannot be empty")


BASELINE_SCHEMA = MigrationIdentity(1, "baseline_canonical_schema")
CONNECTOR_LIFECYCLE_SCHEMA = MigrationIdentity(2, "external_connector_lifecycle")
CONNECTOR_PROJECTION_SCHEMA = MigrationIdentity(3, "external_record_projection")
MIGRATIONS = (
    BASELINE_SCHEMA,
    CONNECTOR_LIFECYCLE_SCHEMA,
    CONNECTOR_PROJECTION_SCHEMA,
)
CURRENT_SCHEMA_VERSION = MIGRATIONS[-1].version
CANONICAL_TABLES = frozenset(
    {
        "documents",
        "atoms",
        "tags",
        "atom_tags",
        "tag_candidates",
        "atom_links",
        "tag_relations",
        "retrieval_events",
        "feedback_events",
        "calibration_signals",
        "weight_events",
        "atom_embeddings",
    }
)
CONNECTOR_TABLES = frozenset(
    {
        "connector_sources",
        "connector_sync_runs",
        "connector_sync_batches",
        "connector_record_objects",
        "connector_records",
        "connector_relations",
        "connector_tombstones",
    }
)
CONNECTOR_PROJECTION_TABLES = frozenset(
    {
        "connector_record_projections",
        "connector_projection_atoms",
        "connector_tombstone_projections",
        "connector_query_receipts",
        "connector_outcome_receipts",
    }
)


class UnsupportedSchemaVersion(RuntimeError):
    """Raised when a database is newer than this application understands."""


def pending_migrations(current_version: int) -> tuple[MigrationIdentity, ...]:
    if current_version < 0:
        raise ValueError("schema version cannot be negative")
    if current_version > CURRENT_SCHEMA_VERSION:
        raise UnsupportedSchemaVersion(
            f"database schema version {current_version} is newer than supported version "
            f"{CURRENT_SCHEMA_VERSION}; use a compatible Cortex release"
        )
    return tuple(migration for migration in MIGRATIONS if migration.version > current_version)

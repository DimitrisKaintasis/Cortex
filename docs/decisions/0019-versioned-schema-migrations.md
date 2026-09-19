# ADR-0019: Versioned, adapter-owned schema migrations

- Status: Accepted for the next schema-changing release
- Date: 2026-09-17
- Refines: ADR-0003 and ADR-0007

## Context

SQLite and PostgreSQL currently create their schemas inside their repository adapters. SQLite
also performs a small set of `_ensure_column` upgrades, while PostgreSQL uses idempotent
`CREATE ... IF NOT EXISTS` and `ALTER ... IF NOT EXISTS` statements. This is adequate for the
local v0.1 database, but it does not persist which logical schema version a database has reached.
As the schema evolves, startup code cannot safely distinguish an old supported database from a
database created by newer, incompatible code.

The two adapters share one logical data model but require different SQL and operational steps.
A migration mechanism must preserve that distinction without allowing the schemas to drift.

## Decision

1. Assign every logical schema change a monotonically increasing integer version and a stable
   migration name. SQLite and PostgreSQL implementations of the same logical change share that
   identity even when their SQL differs.
2. Persist the current version with SQLite's `PRAGMA user_version` and a singleton
   `data_retrieval.schema_metadata` row in PostgreSQL.
3. Move the existing compatibility operations into ordered adapter-owned migration functions.
   A new database is built by applying the same sequence rather than by maintaining a separate
   untested schema snapshot.
4. Apply each migration inside the strongest transaction supported by the adapter. Record the new
   version only after the migration and its invariant checks complete.
5. Refuse to open a database whose recorded version is newer than the running application. Do not
   silently downgrade or attempt best-effort compatibility.
6. Back up and verify PostgreSQL before an upgrade. Preserve a recoverable SQLite database copy
   before a migration that rewrites data or tables. Schema downgrade is restore-based, not an
   automatically generated reverse migration.
7. Test upgrades from every supported historical version, current-version no-op startup, rejection
   of future versions, interrupted-migration rollback, and parity of logical constraints across
   both adapters.
8. For the laptop-only v0.1 deployment, migrations may run during repository startup. Before a
   multi-process or hosted deployment, migration execution moves to an explicit deployment step so
   only one owner can change the schema.

## Implementation boundary

The shared ordered migration registry and baseline migration are implemented. SQLite records the
version with `PRAGMA user_version`, validates the resulting schema, rejects future versions, and
creates an atomic sibling backup before upgrading a non-empty legacy database. PostgreSQL records
the matching logical version and migration name in `data_retrieval.schema_metadata`, applies the
baseline and version update in one transaction, validates its schema, and rejects future versions.

Automated tests cover fresh SQLite creation, version-0 upgrade, current-version no-op startup,
future-version rejection, invariant failure, and interrupted-migration rollback. PostgreSQL has
the same migration identity and a conditional integration assertion, but its live backup/restore
upgrade rehearsal remains an operational gate before the next connector schema change.

## Consequences

- A database version becomes inspectable and upgrade failures become diagnosable.
- SQLite and PostgreSQL can use native mechanisms without pretending their operational behavior is
  identical.
- Every schema change must include two adapter decisions, parity tests, and recovery notes.
- Startup remains convenient during the local phase, while the contract already defines the
  transition to explicit deployment migrations.

## Alternatives

### Adopt Alembic immediately for both adapters

Alembic is a natural option for PostgreSQL, but using it as the SQLite abstraction would not remove
adapter-specific rebuild and compatibility behavior. Introducing it before migration identities and
parity rules are defined would add machinery without settling ownership.

### Continue with idempotent startup SQL only

This is simple but cannot reliably reject future schemas, prove ordered data migrations, or explain
which upgrades have completed.

### Recreate databases from source data

This is acceptable for disposable evaluation stores, but not for feedback events, learned weights,
review decisions, or other canonical operational state that cannot be regenerated safely.

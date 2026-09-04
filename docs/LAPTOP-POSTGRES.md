# Laptop PostgreSQL and Recovery Runbook

Status: live recovery gate passed on 2026-09-05

Architecture decision: [ADR-0018](decisions/0018-laptop-only-recoverable-storage.md)

## Mental model

The deployment has four distinct parts:

| Thing | Role | Is it a backup? |
|---|---|---|
| `pgvector/pgvector` Docker image | Read-only packaged PostgreSQL software | No |
| `postgres` container | Running PostgreSQL process | No |
| `data_retrieval_postgres` volume | Live canonical database files | No |
| `.dump` plus `.dump.json` | Portable logical database archive plus checksum | Yes |

Never back up a running PostgreSQL database by copying its Docker volume directory. Use
`pg_dump`, then prove the archive with `pg_restore`.

## Start laptop PostgreSQL

Keep the real password outside Git. Compose reads the ignored `.env` file, while application
commands need the DSN in their process environment:

```powershell
docker compose -f .\compose.postgres.yml up -d
docker compose -f .\compose.postgres.yml ps

$env:DATA_RETRIEVAL_POSTGRES_DSN = `
  "postgresql://data_retrieval:$($env:DATA_RETRIEVAL_POSTGRES_PASSWORD)@127.0.0.1:5432/data_retrieval"
```

PostgreSQL is reachable only from the laptop at `127.0.0.1:5432`. Docker's
`restart: unless-stopped` restarts the container while Docker Desktop is running; it does not make
the database available while the laptop is off.

Inspect operational state with:

```powershell
docker compose -f .\compose.postgres.yml ps
docker compose -f .\compose.postgres.yml logs --tail 100 postgres
```

## Create a backup

```powershell
python -m data_retrieval postgres-backup
```

The default output is a timestamped custom-format archive under `data/backups/postgres/`. The
command writes to a temporary file first, refuses accidental overwrite, atomically publishes the
archive, and creates an adjacent JSON manifest containing its size and SHA-256 checksum. Neither
the DSN nor database password is passed to `pg_dump`.

Use `--output` for a chosen ignored path. `--overwrite` is available but should be exceptional:

```powershell
python -m data_retrieval postgres-backup `
  --output .\data\backups\postgres\before-upgrade.dump
```

## Prove restoration

Creating a file is not enough. Restore it into an isolated database and validate canonical row
counts:

```powershell
python -m data_retrieval postgres-verify-backup `
  .\data\backups\postgres\before-upgrade.dump
```

The verifier checks the manifest checksum before touching PostgreSQL. It then creates a database
named `data_retrieval_verify_<random>`, restores with `--exit-on-error`, confirms pgvector and
counts every canonical table present in the archive. Older backups may legitimately lack
`weight_events` or `tag_candidates`; the verifier does not migrate them. Missing core tables or
pgvector fail verification. It drops that temporary database
even if restoration fails. It never replaces or modifies the canonical `data_retrieval` database.

## Backup policy for this phase

- Before a schema, image, or dependency upgrade: create and verify one backup.
- During active ingestion: create one backup daily after the run finishes.
- Keep both `.dump` and `.dump.json` together.
- Do not call a same-disk copy disaster recovery. When an external drive or trusted online store
  becomes available, copy verified pairs there.
- Perform a restore test after changing PostgreSQL major version or backup tooling.

Automatic retention/deletion is deliberately absent. It is safer to measure real backup growth
before adding a policy that destroys recovery points.

## Stop and update safely

1. Let the foreground ingestion/API request finish.
2. Create and verify a backup.
3. Stop the application process.
4. Apply code or container-image changes.
5. Start PostgreSQL and run the live integration tests.
6. Start the application and inspect its health endpoint.

`docker compose down` removes the container and network but preserves the named volume unless
`--volumes` is explicitly supplied. Do not use `--volumes` for normal operation.

## Resolved Docker startup blocker

On 2026-09-04, Docker Desktop 4.53 failed before starting its engine because its optional Model
Runner could not replace the stale `%LOCALAPPDATA%\Docker\run\dockerInference` runtime socket.
Disabling the related preference did not resolve startup and was reverted to avoid changing an
unrelated Docker feature. The user's Docker restart resolved the failure on 2026-09-05.
PostgreSQL resumed healthy on loopback with its existing volume intact.

## Live acceptance evidence — 2026-09-05 (Europe/Athens)

Docker Engine 29.0.1 and the existing `pgvector/pgvector:0.8.6-pg17` container were healthy.
The canonical database occupied approximately 1,390 MB before backup. All 176 regression tests
passed with live PostgreSQL enabled (no skipped tests). The integration tests used a separate
database, so their fixtures did not enter the canonical corpus. A real Uvicorn process against
that test database returned HTTP 200 from `/health` with `storage=postgresql` and was stopped.

The existing corpus was backed up without applying application migrations:

- Archive: `data/backups/postgres/data-retrieval-20260904T223440Z.dump` (UTC filename).
- Size: 202,657,780 bytes (about 193 MiB).
- SHA-256: `cab1296a09f3089935878406f354e25aff5f732a03994cae8d76f3b5fd89cbaf`.
- Manifest checksum, full `pg_restore`, constraints/index creation, and pgvector passed.
- All ten restored table counts matched a subsequent read-only count of the live database.

| Table | Restored rows |
|---|---:|
| documents | 26,300 |
| atoms | 272,209 |
| atom_embeddings | 14,435 |
| atom_links | 57,893 |
| atom_tags | 62,902 |
| tags | 17,892 |
| tag_relations | 2 |
| calibration_signals | 303 |
| retrieval_events | 2,278 |
| feedback_events | 2 |

The current-schema integration fixture was separately backed up and restored:

- Archive: `data/backups/postgres/acceptance-current-20260905.dump`.
- SHA-256: `1e77140a776f884294881e6945fbff99ecef28a51793f3b8cfea130e108cd1c7`.
- Restored: 5 documents, 10 atoms, 1 embedding, 10 links, 2 atom-tag edges, 45 calibration
  signals, 2 tags, and 12 weight events; remaining tables were present and empty.

These results prove archive recovery for both schemas. They do not prove identical retrieval
rankings after migration or provide off-device disaster recovery. Both archives and their
manifests remain in ignored laptop storage. Temporary restore/test databases were removed after
verification; the original database and archives were preserved.

## Next: legacy corpus migration rehearsal

The original corpus predates the current tag-candidate and weight-ledger tables. Before opening
it through the current application, restore a working copy and rehearse normal schema
initialization. It can quarantine legacy proposed tags and seed historical weight baselines,
so compare provenance and retrieval before upgrading the canonical database. The backup verifier
only restores and inspects; it never triggers those application migrations.

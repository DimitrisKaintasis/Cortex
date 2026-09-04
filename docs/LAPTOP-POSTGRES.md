# Laptop PostgreSQL and Recovery Runbook

Status: implemented; live recovery gate pending a healthy Docker Desktop engine

Architecture decision: [ADR-0018](decisions/0018-laptop-only-recoverable-storage.md)

## Mental model

There are three different things here:

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
counts documents, atoms, tags, atom-tag edges, and weight events. It drops that temporary database
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

## Current local blocker

On 2026-09-04, Docker Desktop 4.53 failed before starting its engine because its optional Model
Runner could not replace the stale `%LOCALAPPDATA%\Docker\run\dockerInference` runtime socket.
Disabling the related preference did not resolve startup and was reverted to avoid changing an
unrelated Docker feature. The disposable socket still requires cleanup/restart outside the coding
sandbox before the first live restore gate can pass. No PostgreSQL volume or project data was
deleted.

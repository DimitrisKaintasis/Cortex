# ADR-0018: Laptop-only recoverable storage before remote deployment

- Status: Accepted
- Date: 2026-09-04
- Temporarily supersedes: ADR-0004's Mac inference deployment
- Refines: ADR-0007's online PostgreSQL sequence

## Context

The shared Mac is capable of inference, but its storage, administrator trust boundary, and
unattended decryption design are not settled. The user has chosen to keep the system on the
laptop until those decisions are resolved. The project still needs a realistic large-ingestion
database and a recovery path that does not depend on copying live PostgreSQL files.

## Decision

1. Keep all canonical data and current execution on the laptop.
2. Keep SQLite as the zero-service small/medium mode.
3. Run PostgreSQL with pgvector in Docker on laptop loopback for large ingestion and realistic
   retrieval tests.
4. Treat the Docker volume as the live database, not as a backup.
5. Create logical custom-format `pg_dump` archives with a SHA-256 manifest.
6. Prove each backup by restoring it into a uniquely named temporary database, checking the
   vector extension and canonical row counts, and dropping only that isolated database.
7. Keep dumps under ignored local storage. Copy both the dump and manifest to a different
   physical device when one becomes available; a second copy on the same laptop is not protection
   against laptop loss or disk failure.
8. Do not deploy a background worker, public API, remote database, or Mac-held credentials in
   this phase. The local API remains bound to `127.0.0.1` and runs in the foreground.

## Operational boundary

- **Runs on:** the laptop.
- **Starts with:** Docker Desktop, `docker compose`, and an explicit foreground CLI/API command.
- **Stays running:** PostgreSQL uses `restart: unless-stopped` while Docker Desktop is available;
  the application itself does not survive logout, reboot, or laptop shutdown.
- **Configuration:** PostgreSQL password and DSN remain outside Git in process environment/local
  ignored files. Backup commands execute PostgreSQL tools inside the container and do not receive
  the password or DSN as command arguments.
- **Networking:** PostgreSQL and the API bind only to laptop loopback.
- **Logs:** `docker compose logs postgres` for storage and the foreground terminal for the app.
- **Updates:** stop foreground work, create and verify a backup, update code/image deliberately,
  run migrations/tests, then restart.

## Consequences

- Development can continue safely without resolving remote trust first.
- PostgreSQL's bounded-ingestion behavior can be exercised against realistic datasets.
- The laptop remains a single availability point and must be powered on during processing.
- A local dump protects against logical mistakes and container-volume failure, but not theft or
  physical disk failure until it is copied elsewhere.
- D4 autonomous Mac work remains locked behind a later always-reachable storage and security
  decision. This ADR does not reject that deployment.

## Alternatives

### SQLite only

Operationally simpler, but it would stop testing the PostgreSQL path built for large datasets.

### Shared Mac storage now

Cheaper and always available, but the active-host administrator can inspect plaintext during
processing and the external-storage design is not ready.

### Hosted PostgreSQL now

Would enable autonomous workers, but adds a recurring service, remote secrets, TLS/networking,
and backup-provider decisions before they are necessary for laptop development.

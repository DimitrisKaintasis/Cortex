# ADR-0017: Loopback-only local application API

## Status

Accepted and implemented on 2026-09-04.

## Context

The domain services and CLI can ingest, retrieve, explain, review tag candidates, and learn from
explicit outcomes, but a UI or Codex integration would otherwise need to reproduce CLI
orchestration. Hosted storage, user accounts, authentication, and an autonomous Mac worker have
not yet passed their deployment prerequisites.

## Decision

1. Add FastAPI and Uvicorn as an optional `api` dependency, not a core domain dependency.
2. Keep HTTP request models and serialization in one transport module. Existing domain services
   remain authoritative for validation, persistence, retrieval, review, and learning.
3. Bind the CLI server only to `127.0.0.1`; expose no non-loopback option in this phase.
4. Add no CORS, authentication, account, or public deployment behavior to the local server.
5. Open one SQLite/PostgreSQL repository per request and close it after the operation.
6. Accept bounded text ingestion, not arbitrary server-side file paths. Large and processor-rich
   jobs remain in the checkpointed CLI pipeline.
7. Return retrieval score components, evidence, temporal labels, and lineage so consumers do not
   turn explainable retrieval into an opaque search endpoint.
8. Apply feedback and tag decisions only through the existing audited services.

## Alternatives

### Hand-written standard-library HTTP server

This avoids two optional packages but requires custom routing, JSON validation, schema
documentation, and error handling. That code would be larger and less reliable than the thin
transport being added.

### Public hosted API now

This could make remote access immediate, but it would expose private evidence before accounts,
authorization, rate limits, TLS deployment, secrets management, deletion semantics, and incident
procedures exist.

### UI calling the CLI directly

This avoids HTTP but couples a future interface to subprocess output, process lifetimes, and
command parsing. It is useful for scripts, not a clean application boundary.

## Consequences

- A local browser UI, script, or Codex tool can use stable JSON/OpenAPI contracts.
- Core installations do not need FastAPI or Uvicorn.
- The server remains a foreground laptop process and is not remotely available.
- PostgreSQL connection-per-request is adequate for the local MVP but should become a managed
  pool if measured concurrent load warrants it.
- Authentication and public deployment remain separate future decisions rather than accidental
  properties of the MVP.

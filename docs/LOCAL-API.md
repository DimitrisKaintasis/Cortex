# Local API Runbook

Status: local MVP

Architecture decision: [ADR-0017](decisions/0017-loopback-local-api.md)

## Purpose and boundary

The API makes the existing application usable by a local UI, script, or Codex integration
without duplicating domain logic. It exposes:

| Method and path | Capability |
|---|---|
| `GET /health` | Database readiness and non-secret model configuration status |
| `GET /v1/namespaces` | Available canonical namespaces |
| `POST /v1/documents` | Atomic canonical text ingestion with optional explicit tags/time |
| `POST /v1/retrievals` | Explainable hybrid and temporal retrieval |
| `POST /v1/feedback` | Attributable positive or negative learning for returned atoms |
| `GET /v1/tag-candidates` | Quarantined candidate review with source evidence |
| `POST /v1/tag-candidates/{id}/resolution` | Promote, merge, or reject a candidate |
| `POST /v1/sources` | Register or exactly replay one connector source |
| `GET /v1/sources/{system}/{instance}` | Inspect one registered connector source |
| `POST /v1/sync-runs/{run_id}/batches` | Durably submit one replay-safe records/relations/tombstones batch |
| `GET /v1/sync-runs/{run_id}` | Inspect the accepted sync-run contract |
| `POST /v1/sync-runs/{run_id}:commit` | Advance the opaque connector cursor after durable batches |

This is not a public API. It has no accounts, authentication, authorization, rate limiting, or
cross-origin browser access. The CLI fixes the listener to laptop loopback, `127.0.0.1`, and does
not offer a public bind flag. Do not place it behind a public reverse proxy.

## Install and start

Install the optional API transport:

```powershell
python -m pip install -e ".[api]"
```

Start against laptop SQLite:

```powershell
python -m data_retrieval serve-api `
  --db .\data.sqlite3 `
  --port 8765
```

Or use PostgreSQL without putting the DSN in command history:

```powershell
python -m pip install -e ".[api,postgres]"
$env:DATA_RETRIEVAL_POSTGRES_DSN = "<protected PostgreSQL DSN>"
python -m data_retrieval serve-api --port 8765
```

Open `http://127.0.0.1:8765/docs` for the generated interactive OpenAPI interface. The server
runs in the foreground and stops with `Ctrl+C`; it does not yet survive laptop shutdown or login.
The connector routes publish the transport-independent Source and SyncBatch shapes in that
OpenAPI document. They remain local-user-trust endpoints, not remotely authorized routes.

Install `.[sdk]` in the connector process to use the typed `cortex.CortexClient`. See the
[connector SDK quickstart](CONNECTOR-SDK.md). The SDK never retries or commits a sync implicitly;
the connector owns repair policy and advances its upstream cursor only after a commit
acknowledgement.

## Optional laptop-local inference

The API performs lexical, tag-graph, relationship, and temporal retrieval without a live model.
Configure laptop-local Ollama to add generated query tags and semantic search:

```powershell
python -m data_retrieval serve-api `
  --db .\data.sqlite3 `
  --ollama-url http://127.0.0.1:11434 `
  --tag-model gemma4:e2b-mlx `
  --embedding-model hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16 `
  --embedding-profile harrier-retrieval-v1
```

Ollama now defaults to laptop-local `127.0.0.1:11434`. If no local model is available, omit model
options; lexical, explicit-tag, relationship, and temporal retrieval remain available. An API
provider is a separate privacy decision because source/query content leaves the laptop.

## Operational model

- The API process runs on the laptop.
- SQLite or PostgreSQL remains canonical; one repository connection is opened and closed per
  request so transaction ownership is explicit.
- The PostgreSQL DSN and model endpoint are startup configuration. Neither is returned by the
  API.
- Uvicorn writes access and application errors to the terminal.
- Restart the foreground process to deploy code or configuration changes.
- Back up the database, not API process state. Retrieval and feedback events live in the
  canonical repository.

`POST /v1/documents` intentionally accepts text rather than arbitrary laptop file paths and caps
one request at 2,000,000 characters. Large files and full Tags/Temporal/Mem0 enrichment should
continue through the checkpointed `process-file` command, using PostgreSQL for bounded ingestion.

## Minimal use sequence

1. Submit text to `POST /v1/documents`.
2. Query it through `POST /v1/retrievals`.
3. Inspect each result's channel score, evidence strings, temporal label, and lineage IDs.
4. Send only actually used returned atom IDs to `POST /v1/feedback`.
5. Review AI-proposed tags through the candidate endpoints before they enter retrieval.

Feedback cannot credit an arbitrary atom: the learning service verifies that every selection was
returned by the referenced retrieval. Candidate promotion and rejection use the same atomic
lifecycle service as the CLI.

## Next boundary

The local API completes D2 but does not make the system always online. D3a is recoverable laptop
PostgreSQL, described in `LAPTOP-POSTGRES.md`. D3b always-reachable storage and D4 autonomous Mac
work are deferred until their storage and active-host trust boundaries are accepted.

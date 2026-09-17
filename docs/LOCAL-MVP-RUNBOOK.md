# Local MVP Runbook

Status: implemented and tested locally

Deployment boundary: laptop-owned data and execution

## What exists now

`process-file` turns the existing processors into one observable job. The stages run in this
order:

1. canonical atom ingestion;
2. optional tag proposals;
3. optional Temporal History projection;
4. optional Mem0 bootstrap;
5. optional atom embeddings;
6. optional Mem0/vector corroboration;
7. immutable weight-ledger audit.

Canonical ingestion always runs. A derived stage runs only when its model or Mem0 option is
configured; otherwise the report records it as `skipped`. The command never silently invents a
model choice.

Every stage transition is written to an atomic JSON checkpoint. If a provider fails, canonical
data and already completed idempotent work remain in the database, the report records the exact
failed stage, and repeating the same command safely reuses completed work.

## Where each part runs

| Part | Current location | Persistence |
|---|---|---|
| CLI and orchestration | Laptop, foreground process | Source code in this repository |
| Canonical atoms, tags, links, events | Laptop SQLite or laptop PostgreSQL | Database file or Docker volume |
| Temporal History state | Laptop | Rebuildable SQLite state file |
| Mem0/Kuzu/vector working state | Laptop | Rebuildable processor state configured by Mem0 |
| Tag, Temporal, embedding, and Mem0 model inference | Laptop-local provider when explicitly configured | Provider-specific working state |
| Pipeline status and errors | Laptop | Atomic JSON run report plus terminal stderr |

The laptop must remain on and the foreground command must remain running. There is no daemon,
durable job lease, or autonomous Mac worker. The loopback API is optional and also runs in the
foreground.

## Start the pipeline

Canonical-only smoke run:

```powershell
python -m data_retrieval process-file .\notes.txt `
  --db .\data.sqlite3 `
  --namespace personal `
  --source notes
```

Tag and embedding enrichment through laptop-local Ollama:

```powershell
python -m data_retrieval process-file .\notes.txt `
  --db .\data.sqlite3 `
  --namespace personal `
  --source notes `
  --ollama-url http://127.0.0.1:11434 `
  --tag-model gemma4:e2b-mlx `
  --embedding-model hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16 `
  --embedding-profile harrier-retrieval-v1
```

Add Temporal processing only for timestamped evidence. The half-open range must contain the
source timestamp:

```powershell
python -m pip install -e ".[temporal]"

python -m data_retrieval process-file .\notes.txt `
  --db .\data.sqlite3 `
  --namespace personal `
  --source notes `
  --occurred-at 2026-09-04T10:00:00+03:00 `
  --timeline-id main `
  --range-start 2026-09-04T00:00:00+03:00 `
  --range-end 2026-09-05T00:00:00+03:00 `
  --timezone Europe/Athens `
  --tag-model gemma4:e2b-mlx `
  --temporal-model gemma4:e2b-mlx `
  --embedding-model hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16 `
  --embedding-profile harrier-retrieval-v1
```

For Mem0, install the optional dependency and explicitly enable it. Supplying a config also
enables the stage:

```powershell
python -m pip install -e ".[mem0,postgres]"

python -m data_retrieval process-file .\notes.txt `
  --postgres-dsn $env:DATA_RETRIEVAL_POSTGRES_DSN `
  --namespace personal `
  --source notes `
  --tag-model gemma4:e2b-mlx `
  --embedding-model qwen3-embedding:0.6b `
  --mem0-config .\config\mem0.local.json
```

By default the checkpoint is `data/runs/<stable-run-id>.json`. Use `--report` for a chosen path.
The stable ID includes the input bytes and configured processor profile, so a changed file or
model configuration gets a different report identity.

## Storage modes are deliberately different

- SQLite is the zero-service local/test mode. Its canonical write is atomic, but one plain-text
  input is read into laptop memory before chunking. Use it for small and medium files.
- PostgreSQL is the scale mode. It performs two-pass streaming, writes bounded atom batches,
  hides staging documents, and publishes only after verifying the final atom count. Use it for
  large files and benchmark adapters.

The report exposes `ingestion_mode` as `atomic-in-memory` or `bounded-staged`; this boundary must
not be blurred in performance or reliability claims.

## Configuration and secrets

- Ollama defaults to laptop-local `http://127.0.0.1:11434`; override it explicitly only when the
  deployment boundary changes.
- Set OpenRouter credentials only through `OPENROUTER_API_KEY`; reports contain provider/model
  identities but no API key or database DSN.
- Keep Mem0 provider credentials in environment variables, not committed JSON.

## Inspect and retry

The terminal shows command-level failures. The JSON report shows each stage as `running`,
`completed`, `skipped`, or `failed`, including timings and the local error type/message. Treat
reports as operational data rather than public artifacts.

To retry, restore the same local provider/configuration and repeat the same command. Canonical
document IDs, tag markers, embedding content hashes, Temporal state, and Mem0 calibration markers
provide the real idempotency; the JSON checkpoint is an operator record, not an alternative
database.

## Laptop-only acceptance smoke — 2026-09-04

A fresh ignored SQLite database processed a one-atom deployment note entirely through laptop
Ollama `0.31.1`:

- `qwen3:8b` completed tag enrichment in about 61 seconds and produced six quarantined candidates;
- `nomic-embed-text:latest` embedded the atom in about 13 seconds;
- the immutable weight-ledger audit passed;
- a natural-language upgrade-safety query returned the correct atom at rank 1 through both lexical
  and semantic channels (`semantic=0.6048`) with no diagnostic warning;
- the real loopback API returned HTTP 200 and reported one namespace, then shut down cleanly.

The ignored evidence is under `data/smoke/`. This proves local wiring and recovery behavior, not
that these already-installed laptop models are the final quality/performance choices. Temporal and
Mem0 were correctly skipped because this deployment fixture did not configure them.

## Next deployment boundary

The local retrieval and feedback API is implemented in `LOCAL-API.md`. The active boundary is
recoverable laptop PostgreSQL with checksummed backups and tested restoration, documented in
`LAPTOP-POSTGRES.md`. Always-reachable storage and a leased Mac worker are deferred.

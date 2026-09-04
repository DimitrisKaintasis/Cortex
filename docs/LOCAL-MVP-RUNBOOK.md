# Local MVP Runbook

Status: implemented and tested locally

Deployment boundary: laptop-owned data with on-demand Mac inference

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
| Tag, Temporal, embedding, and Mem0 model inference | Mac Ollama through the SSH tunnel | Models on Mac; no canonical data |
| Pipeline status and errors | Laptop | Atomic JSON run report plus terminal stderr |

The laptop must remain on and the foreground command must remain running. There is no daemon,
API, durable job lease, or autonomous Mac worker yet. The Mac becomes idle when the command or
SSH tunnel stops.

## Start the pipeline

Canonical-only smoke run:

```powershell
python -m data_retrieval process-file .\notes.txt `
  --db .\data.sqlite3 `
  --namespace personal `
  --source notes
```

Tag and embedding enrichment through Mac Ollama:

```powershell
python -m data_retrieval process-file .\notes.txt `
  --db .\data.sqlite3 `
  --namespace personal `
  --source notes `
  --tag-model gemma4:e2b-mlx `
  --embedding-model hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16 `
  --embedding-profile harrier-retrieval-v1
```

Add Temporal processing only for timestamped evidence. The half-open range must contain the
source timestamp:

```powershell
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
python -m pip install -e ".[mem0]"

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

- Ollama defaults to the laptop tunnel endpoint `http://127.0.0.1:11435`.
- Keep the SSH private key private and outside Git. The command never needs to read or print it.
- Set OpenRouter credentials only through `OPENROUTER_API_KEY`; reports contain provider/model
  identities but no API key or database DSN.
- Keep Mem0 provider credentials in environment variables, not committed JSON.

## Inspect and retry

The terminal shows command-level failures. The JSON report shows each stage as `running`,
`completed`, `skipped`, or `failed`, including timings and the local error type/message. Treat
reports as operational data rather than public artifacts.

To retry, restore the same tunnel/configuration and repeat the same command. Canonical document
IDs, tag markers, embedding content hashes, Temporal state, and Mem0 calibration markers provide
the real idempotency; the JSON checkpoint is an operator record, not an alternative database.

## Next deployment boundary

The local retrieval and feedback API is now implemented in `LOCAL-API.md`. The next boundary is a
TLS-protected hosted PostgreSQL database with tested backup and restoration. A leased job queue
can then let a Mac worker continue after the laptop disconnects.

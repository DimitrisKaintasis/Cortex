# Data Retrieval

The authoritative component boundaries, processor contracts, and isolated capability-testing
order are documented in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Mem0 and Temporal
History are replaceable processors; Data Retrieval owns canonical evidence and final retrieval.
The final context-composition constraints are documented in
[`docs/EVIDENCE-PACKING.md`](docs/EVIDENCE-PACKING.md). The deterministic subsystem test
harness and its limitations are documented in
[`docs/ISOLATED-CAPABILITY-GATES.md`](docs/ISOLATED-CAPABILITY-GATES.md). Recovered historical
scope, current gaps, anti-goals, and the dependency-ordered repair plan are recorded in
[`docs/ARCHITECTURE-RECONCILIATION.md`](docs/ARCHITECTURE-RECONCILIATION.md).

Data Retrieval is a clean successor to the original `Tags-Project`. It is a
tag-centric retrieval engine that keeps source order, tag provenance, and
retrieval scoring explainable.

The project is being migrated deliberately from two references:

- `DimitrisKaintasis/Tags-Project` — the original 2024 prototype.
- `DimitrisKaintasis/DebUI/subprojects/data-memory` — the later Cortex design.

We are preserving the useful ideas without copying either implementation
wholesale.

## Current milestone

The repository currently provides a dependency-free domain slice for:

- deterministic document and atom identities;
- ordered, overlapping text chunks with character offsets;
- normalized and deduplicated tags;
- explicit tag provenance and proposal state;
- atomic, idempotent raw ingestion through a repository interface;
- repeatable atom-level tag enrichment from a remote Ollama model;
- timestamped source atoms and generic atom-to-atom lineage;
- a pinned Temporal History bridge and Ollama summarizer that produce auditable
  calendar-summary atoms from stored source atoms;
- a durable SQLite repository with transactions, foreign keys, and full hydration;
- a PostgreSQL/pgvector adapter for canonical online storage;
- bounded indexed retrieval candidates instead of namespace-wide hydration;
- restart-safe, bounded-memory PostgreSQL ingestion for large UTF-8 files;
- an in-memory repository for fast tests.
- explainable tag, lexical, semantic, learned-relationship, and temporal retrieval;
- content-hash-aware embedding enrichment through a replaceable Ollama model;
- recorded retrieval and feedback events with bounded, atomic learning updates;
- replay-safe teacher calibration and a first-class Mem0 bootstrap/training bridge;
- interaction atoms with selected-evidence lineage and attributable outcome learning;
- a versioned evaluation corpus for tag, semantic, and temporal behavior.

## Development

Python 3.13 or newer is required.

```powershell
python -m unittest discover -s tests -v
```

Run the seven isolated architecture contracts before combined evaluation:

```powershell
python -m data_retrieval evaluate-capabilities
```

Optional developer tools can be installed with:

```powershell
python -m pip install -e ".[dev]"
```

### PostgreSQL scale mode

SQLite remains the default for local development. PostgreSQL is selected only when
`--postgres-dsn` or `DATA_RETRIEVAL_POSTGRES_DSN` is provided. The DSN is never returned
in CLI output.

For a local development database, set a non-committed password and start the pinned
pgvector image:

```powershell
$env:DATA_RETRIEVAL_POSTGRES_PASSWORD = "replace-with-a-long-random-password"
docker compose -f .\compose.postgres.yml up -d

$env:DATA_RETRIEVAL_POSTGRES_DSN = `
  "postgresql://data_retrieval:$($env:DATA_RETRIEVAL_POSTGRES_PASSWORD)@127.0.0.1:5432/data_retrieval"
```

The container runs PostgreSQL on the laptop, binds only to laptop localhost, persists data
in the `data_retrieval_postgres` Docker volume, and restarts unless explicitly stopped.
The repository initializes its private `data_retrieval` schema and requires permission to
create the `vector` extension. Inspect service errors with:

```powershell
docker compose -f .\compose.postgres.yml ps
docker compose -f .\compose.postgres.yml logs postgres
```

With the DSN set, the normal commands use PostgreSQL. File ingestion automatically uses
two-pass streaming and bounded transactions; the first pass computes the stable content
hash and the second creates deterministic atom batches:

```powershell
python -m data_retrieval ingest .\large-dataset.txt `
  --namespace benchmarks `
  --source longmemeval/oracle `
  --batch-size 1000
```

An interrupted document remains hidden in `staging`. Repeating the same command resumes by
idempotently upserting the same atom IDs and publishes the document only after verifying
its final atom count. PostgreSQL retrieval uses GIN full-text search, indexed tag joins,
and pgvector similarity to bound each initial channel to 500 candidates before learned
relationship and temporal processing.

This command is the bounded-memory path for plain UTF-8 text; it is not a benchmark-format
adapter. LongMemEval, EverMemBench, and WikiConv need streaming JSON/JSONL adapters that
preserve conversations, timestamps, participant IDs, and source record boundaries. Those
adapters should create many reasonably sized documents and run tag enrichment per document
and Temporal enrichment per bounded time window, rather than treating a whole benchmark
archive as one document.

LongMemEval now has that dedicated adapter:

```powershell
python -m data_retrieval ingest-longmemeval `
  .\data\benchmarks\longmemeval\longmemeval_s_cleaned.json `
  --dataset-id cleaned-s-2025-09
```

See the [LongMemEval ingestion report](docs/LONGMEMEVAL.md) for the representation, label
leakage controls, exact dataset hashes, capacity measurements, and current quality boundary.

The Docker definition is a development deployment, not the final always-online database.
Before production use, choose hosted storage, require TLS, restrict network access, store
the DSN in a secret manager, schedule `pg_dump`/provider backups, and test restoration. The
Mac should connect to that protected database as a worker; do not expose port 5432 directly
to the public internet.

### Laptop storage with Mac inference

The current runtime deliberately has no background ingestion worker. PostgreSQL itself is a
Docker service so stored data remains available while Docker Desktop is running:

```text
Laptop file -> raw ingestion -> laptop SQLite or PostgreSQL volume
                                  |
                                  +-> tag enrichment ------> Ollama on Mac
                                  |
                                  +-> Temporal enrichment -> Ollama on Mac
                                  |
                                  +-> embedding inference -> Ollama on Mac
```

- The laptop reads source files, creates canonical atoms, and owns SQLite or the PostgreSQL
  Docker volume.
- The Mac runs Ollama for optional tag proposals and Temporal summaries.
- The SSH tunnel protects the network connection; it does not make the friend's Mac a
  private machine. Do not send data there unless it is acceptable for that machine's
  administrator to process it.
- When the laptop or tunnel is off, no ingestion runs and the Mac remains idle.
- If Ollama fails or returns invalid data, raw ingestion remains safely persisted. The
  failed enrichment can be retried later.

Open the tunnel in one PowerShell window. Replace the placeholders with the local key,
SSH account, and host supplied by the Mac administrator:

```powershell
ssh -i <private-key-path> `
  -o IdentitiesOnly=yes `
  -o KexAlgorithms=curve25519-sha256 `
  -o StrictHostKeyChecking=yes `
  -o ExitOnForwardFailure=yes `
  -N -L 127.0.0.1:11435:127.0.0.1:11434 `
  <ssh-user>@<ssh-host>
```

Keep that window open when using AI enrichment. Raw ingestion itself does not need the
Mac or tunnel.

First ingest a UTF-8 text file. `occurred-at` is required only when the source should
participate in Temporal summaries:

```powershell
python -m data_retrieval ingest .\notes.txt `
  --db .\data.sqlite3 `
  --namespace personal `
  --tag notes `
  --occurred-at 2026-08-19T12:00:00+00:00 `
  --timeline-id main
```

The command returns a stable `document_id`. Use it for optional tag enrichment:

```powershell
python -m data_retrieval enrich-tags <document-id> `
  --db .\data.sqlite3 `
  --ollama-model gemma4:12b-mlx
```

Existing catalog matches attach immediately. A novel model suggestion is stored as a
quarantined candidate and cannot affect retrieval or learned weights. Review pending candidates:

```powershell
python -m data_retrieval list-tag-candidates `
  --db .\data.sqlite3 `
  --namespace personal `
  --state proposed

python -m data_retrieval resolve-tag-candidate <candidate-id> `
  --db .\data.sqlite3 `
  --action promote
```

Use `--action merge --canonical-tag <existing-tag>` to reuse an existing concept, or
`--action reject --reason "too vague"` to reject it. Resolution is atomic: accepted tags and
edges appear together, while rejected candidates never enter the serving graph.

Create Temporal summaries from all matching stored source atoms in a half-open time range:

```powershell
python -m data_retrieval enrich-temporal `
  --db .\data.sqlite3 `
  --namespace personal `
  --timeline-id main `
  --range-start 2026-08-19T00:00:00+00:00 `
  --range-end 2026-08-20T00:00:00+00:00 `
  --timezone Europe/Bucharest `
  --ollama-model gemma4:12b-mlx
```

Both enrichments are repeatable. Tag enrichment records its provider/prompt version on
the document; Temporal History maintains a sibling state database and reuses unchanged
period summaries. Temporal topics remain summary metadata until a later calibrated step
explicitly promotes them into the tag graph.

Embed changed atoms once with the selected model. The vectors stay in laptop SQLite;
only inference crosses the SSH tunnel:

```powershell
python -m data_retrieval enrich-embeddings `
  --db .\data.sqlite3 `
  --namespace personal `
  --embedding-model hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16 `
  --embedding-profile harrier-retrieval-v1
```

The embedding identity and source content hash are stored with every vector. Repeating
the command reuses unchanged vectors. Selecting a different model creates that model's
vectors without making the model a code dependency.

Run hybrid retrieval with explicit tags when the caller already knows them:

```powershell
python -m data_retrieval retrieve "current Docker status" `
  --db .\data.sqlite3 `
  --namespace personal `
  --tag docker `
  --timeline-id main `
  --temporal-mode current_state `
  --embedding-model hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16 `
  --embedding-profile harrier-retrieval-v1
```

Or let the optional Ollama tag proposer map the natural-language query into the existing tag
catalog before retrieval:

```powershell
python -m data_retrieval retrieve "How is inference hosted?" `
  --db .\data.sqlite3 `
  --namespace personal `
  --tag-model $env:OLLAMA_TAG_MODEL `
  --embedding-model $env:OLLAMA_EMBEDDING_MODEL
```

The embedding model enables semantic matching from generated concepts to canonical catalog
tags. If the tag model or Mac is unavailable, retrieval continues through explicit tags,
lexical search, and the semantic channel and records the degradation in diagnostics.

The output includes a `retrieval_id`, per-channel scores, evidence, temporal roles, and
atom IDs. Explicit outcome feedback can then update only learned relationships:

```powershell
python -m data_retrieval feedback <retrieval-id> `
  --db .\data.sqlite3 `
  --selected-atom <atom-id> `
  --outcome positive `
  --reason "used in the final answer"
```

Feedback never rewrites source atoms or factual `SUPERSEDES`, `SUMMARIZES`,
`SUPPORTED_BY`, and `DERIVED_FROM` links. It makes small bounded changes to atom-tag weights,
learned `CO_USED` atom links, and tag co-occurrence relations. Summary selections pass only
partial credit to their source lineage.

All edge-weight changes are recorded as immutable events while `weight_raw` remains a fast
serving aggregate. Audit one namespace without changing it:

```powershell
python -m data_retrieval audit-weights `
  --db .\data.sqlite3 `
  --namespace personal
```

If an audit reports only aggregate mismatches, explicitly restore those caches with
`--repair-aggregates`. Broken chains or missing history block repair rather than inventing
events. Run repair while ingestion/learning workers for that namespace are stopped. Existing
databases receive a labeled migration baseline when first opened by this version; it preserves
the current starting weights while marking older detail unavailable.

Import a Mem0 JSON/JSONL export. Records become native atoms; the embedding model enables
the accepted 0.92 semantic near-duplicate check, and the optional tag model enriches records
that do not already contain tags:

```powershell
python -m data_retrieval import-mem0 .\mem0-export.json `
  --postgres-dsn $env:DATA_RETRIEVAL_POSTGRES_DSN `
  --namespace personal `
  --embedding-model $env:OLLAMA_EMBEDDING_MODEL `
  --tag-model $env:OLLAMA_MODEL
```

Existing native atoms can also be distilled through self-hosted Mem0. The job reads one
bounded batch at a time, sends only conversation content plus internal lineage identifiers,
aligns each distilled fact to a bounded subset of source atoms, creates factual `SUPPORTED_BY`
links, and preserves the wider batch only as audit metadata. An output that cannot be aligned
is counted as `memories_unaligned` and is not imported. A rerun resumes completed batches
without calling Mem0 or changing weights again:

```powershell
python -m pip install -e ".[mem0]"

python -m data_retrieval bootstrap-mem0 `
  --postgres-dsn $env:DATA_RETRIEVAL_POSTGRES_DSN `
  --namespace-prefix "longmemeval:" `
  --mem0-config .\config\mem0.local.json `
  --max-documents 10 `
  --embedding-model $env:OLLAMA_EMBEDDING_MODEL
```

Use a small cap first. The Mem0 configuration chooses its LLM, embedder, and temporary working
store; provider credentials belong in environment variables, not the JSON file. In the current
laptop/Mac arrangement, this command and both canonical stores run on the laptop while the Mem0
LLM/embedder URLs point through the SSH tunnel to Mac Ollama. Thus the Mac performs inference but
does not become the authoritative data store. See
[the Mem0 bootstrap runbook](docs/MEM0-BOOTSTRAP.md).

Backfill data ingested before calibration was enabled. This does not regenerate embeddings:

```powershell
python -m data_retrieval backfill-calibration `
  --postgres-dsn $env:DATA_RETRIEVAL_POSTGRES_DSN `
  --namespace-prefix longmemeval: `
  --max-documents 100
```

Record a turn and an attributable successful outcome:

```powershell
python -m data_retrieval record-interaction `
  --postgres-dsn $env:DATA_RETRIEVAL_POSTGRES_DSN `
  --namespace <namespace> `
  --conversation-id <conversation> --turn-id <turn> `
  --user-text "..." --assistant-text "..." `
  --retrieval-id <retrieval-id> --used-atom <atom-id> --outcome positive
```

Run the checked-in regression corpus with or without embeddings:

```powershell
python -m data_retrieval evaluate --db :memory:

python -m data_retrieval evaluate --db :memory: `
  --embedding-model hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16 `
  --embedding-profile harrier-retrieval-v1
```

On 2026-08-20, the expanded nine-case no-embedding baseline scored 55.6% hit@1.
Harrier F16 and `qwen3-embedding:0.6b` both scored 100% hit@1 with zero temporal
forbidden-result violations. Harrier completed the small warmed run in 2.37 seconds
versus Qwen's 3.01 seconds and is the current default because its upstream model has
the stronger current multilingual benchmark. This is an integration baseline, not a
production-quality claim; real failures should be added to the corpus.

`--metadata-json` accepts source-specific fields without coupling ingestion to one chat
or document provider. Temporal History currently recognizes fields including
`event_type`, `recorded_at`, `actor_id`, `actor_display_name`, `actor_type`,
`source_message_id`, thread and parent IDs, `supersedes_event_id`, and
`payload_reference`. Source adapters should populate these when the original system
provides them; ordinary text files can omit them.

The Ollama endpoint defaults to `http://127.0.0.1:11435`; `--ollama-url` can override it.
CLI errors are written to the laptop terminal. Ollama application/service logs remain on
the Mac.

## Architecture

The authoritative target is [Data Retrieval architecture](docs/ARCHITECTURE.md). Its long-term
collective scope, privacy boundary, unbounded support, relative influence, passive decay, and
cross-user validation gate are specified in the
[collective capability graph](docs/COLLECTIVE-CAPABILITY-GRAPH.md),
[ADR-0013](docs/decisions/0013-privacy-preserving-collective-capability-graph.md), and
[ADR-0014](docs/decisions/0014-unbounded-relative-support-and-forgetting.md).

The target graph is:

```text
Document -[:CONTAINS {position}]-> Atom
Atom     -[:HAS_TAG {weight_raw, confidence, origin}]-> Tag
Atom     -[:LINKS_TO {relation}]-> Atom
Tag      -[:RELATED_TO {relation_type, weight_raw}]-> Tag
```

SQLite is the first canonical adapter. Temporal History is a derived projection behind
one adapter, while the Ollama tag proposer and future embedding providers remain
replaceable boundaries rather than being imported throughout the application.

See [the migration plan](docs/MIGRATION.md) and
[ADR-0001](docs/decisions/0001-focused-tag-core.md) and
[ADR-0002](docs/decisions/0002-temporal-history-as-atom-projection.md), and
[ADR-0003](docs/decisions/0003-sqlite-first-canonical-storage.md). The temporary
laptop/Mac split is recorded in
[ADR-0004](docs/decisions/0004-laptop-storage-mac-inference.md), and the durable
raw-ingestion/enrichment boundary in
[ADR-0005](docs/decisions/0005-raw-ingestion-before-enrichment.md).
The staged retrieval and conservative feedback policy are recorded in
[ADR-0006](docs/decisions/0006-staged-retrieval-and-outcome-learning.md).
The PostgreSQL scale target and its staged-ingestion boundary are recorded in
[ADR-0007](docs/decisions/0007-postgresql-scale-target.md).
The first real Mac model comparison and current default are recorded in the
[tag proposal model benchmark](docs/MODEL-BENCHMARK.md).
The current semantic model choice and its reproducibility caveats are recorded in the
[embedding model benchmark](docs/EMBEDDING-BENCHMARK.md).
The replay-safe Mem0 calibration boundary and reverse bridge are recorded in
[ADR-0008](docs/decisions/0008-replayable-calibration-and-mem0.md).

The first live test of the complete ingestion, Tags, Temporal History, hybrid retrieval,
and explicit-feedback loop passed all eight project-history scenarios. See the
[project-history acceptance report](docs/PROJECT-HISTORY-ACCEPTANCE.md) for what it proves,
how to reproduce it, and the answer-packing limitation it exposed.

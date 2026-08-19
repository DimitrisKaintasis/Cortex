# Data Retrieval

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
- an in-memory repository for fast tests.
- explainable tag, lexical, semantic, learned-relationship, and temporal retrieval;
- content-hash-aware embedding enrichment through a replaceable Ollama model;
- recorded retrieval and feedback events with bounded, atomic learning updates;
- a versioned evaluation corpus for tag, semantic, and temporal behavior.

## Development

Python 3.13 or newer is required.

```powershell
python -m unittest discover -s tests -v
```

Optional developer tools can be installed with:

```powershell
python -m pip install -e ".[dev]"
```

### Laptop storage with Mac inference

The current runtime deliberately has no background worker:

```text
Laptop file -> raw ingestion -> laptop SQLite
                                  |
                                  +-> tag enrichment ------> Ollama on Mac
                                  |
                                  +-> Temporal enrichment -> Ollama on Mac
                                  |
                                  +-> embedding inference -> Ollama on Mac
```

- The laptop reads source files, creates canonical atoms, and owns SQLite.
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

The output includes a `retrieval_id`, per-channel scores, evidence, temporal roles, and
atom IDs. Explicit outcome feedback can then update only learned relationships:

```powershell
python -m data_retrieval feedback <retrieval-id> `
  --db .\data.sqlite3 `
  --selected-atom <atom-id> `
  --outcome positive `
  --reason "used in the final answer"
```

Feedback never rewrites source atoms or factual `SUPERSEDES`, `SUMMARIZES`, and
`DERIVED_FROM` links. It makes small bounded changes to atom-tag weights, learned
`CO_USED` atom links, and tag co-occurrence relations. Summary selections pass only
partial credit to their source lineage.

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
The first real Mac model comparison and current default are recorded in the
[tag proposal model benchmark](docs/MODEL-BENCHMARK.md).
The current semantic model choice and its reproducibility caveats are recorded in the
[embedding model benchmark](docs/EMBEDDING-BENCHMARK.md).

The first live test of the complete ingestion, Tags, Temporal History, hybrid retrieval,
and explicit-feedback loop passed all eight project-history scenarios. See the
[project-history acceptance report](docs/PROJECT-HISTORY-ACCEPTANCE.md) for what it proves,
how to reproduce it, and the answer-packing limitation it exposed.

# Mem0 entity-graph bootstrap runbook

## What this integration does

The canonical Cortex ingestion runs first and creates source evidence atoms and tags. The
bootstrap then gives those same atom contents to an ordinary self-hosted Mem0 `Memory.add`
call with inference enabled. Mem0 continues to maintain its normal fact memories and entity
graph, but Cortex imports only a provenance-bearing entity projection:

```text
source evidence atoms
    -> normal Mem0 inference
         -> normal Mem0 facts and graph (rebuildable working state)
         -> entity relationships carrying exact source-atom IDs
         -> private entity-mention atoms in Cortex
         -> SUPPORTED_BY -> source evidence atoms
         -> inactive MEM0_ENTITY_RELATION proposal -> explicit admission
```

Entity atoms receive no copied tags. New relationship proposals start at weight zero; the
separate vector calibration command may grant only bounded provisional weight. Retrieval reaches
source evidence through admitted bounded graph paths. This prevents a private proper name such as
`Alice` from becoming a globally shared tag that could collide with another user's unrelated
Alice.

## What runs where

- **Laptop:** the Data Retrieval CLI, canonical PostgreSQL data, and rebuildable Mem0
  Qdrant/SQLite/Kuzu working files.
- **Mac Mini:** Ollama inference, reached through the existing SSH tunnel.
- **Retrieval runtime:** reads only native PostgreSQL objects. It does not call Mem0, Kuzu,
  Qdrant, Ollama, or the Mac.

PostgreSQL remains the source of truth. Deleting Mem0's working files loses a cache and requires
a rebuild; it does not lose the canonical evidence atoms already in PostgreSQL.

## Configure Mem0 on the laptop

Install the optional adapter and Mem0 graph dependencies:

```powershell
python -m pip install -e ".[mem0]"
```

Create an uncommitted `config/mem0.local.json`. This example uses embedded Kuzu rather than a
separate graph server. The tested Mac-compatible model pair is `gemma4:e2b-mlx` and the
1024-dimensional `qwen3-embedding:0.6b`:

```json
{
  "vector_store": {
    "provider": "qdrant",
    "config": {
      "collection_name": "data_retrieval_mem0",
      "path": "data/mem0/qdrant",
      "on_disk": true,
      "embedding_model_dims": 1024
    }
  },
  "graph_store": {
    "provider": "kuzu",
    "config": {
      "db": "data/mem0/kuzu"
    }
  },
  "history_db_path": "data/mem0/history.sqlite3",
  "llm": {
    "provider": "ollama",
    "config": {
      "model": "gemma4:e2b-mlx",
      "temperature": 0.1,
      "ollama_base_url": "http://127.0.0.1:11435"
    }
  },
  "embedder": {
    "provider": "ollama",
    "config": {
      "model": "qwen3-embedding:0.6b",
      "embedding_dims": 1024,
      "ollama_base_url": "http://127.0.0.1:11435"
    }
  }
}
```

Port `11435` is the laptop end of the SSH tunnel; Ollama remains on Mac port `11434`. Do not
put SSH keys, passwords, or API keys in this file.

## The deliberately small Mem0 extension

The installed Mem0 package is not edited. Data Retrieval wraps one `Memory` instance and changes
only relationship extraction for calls made by this bootstrap:

1. Mem0 entity extraction receives the original unmodified text.
2. Relationship extraction additionally sees an opaque marker before each evidence atom.
3. Its tool schema asks for the exact evidence IDs supporting each relationship.
4. Those fields are removed before Mem0 stores its ordinary triples.
5. Cortex accepts only IDs from the exact request; missing, invented, or malformed provenance is
   quarantined rather than guessed with lexical matching, vectors, or another model call.

Calls outside the adapter continue through Mem0's original graph path. The adapter checks the
private graph methods it relies on and fails clearly if an incompatible Mem0 version is installed;
the optional dependency is therefore pinned to the tested Mem0 1.0.1 release. Upgrading Mem0 is a
deliberate compatibility task that must rerun the deterministic tests and the live quality gate.

Mem0 1.0.1 also accepts Ollama tool schemas without forwarding them and discards Ollama's native
tool calls. The boundary contains a transport-only compatibility shim that forwards Mem0's
unchanged entity/relationship tools and normalizes the native response. Non-tool Mem0 calls are
unchanged. `qwen3.5:4b` failed the relationship schema in the real smoke test;
`gemma4:e2b-mlx` passed it and is the current local proposal model for this stage. It failed the
strict labeled semantic promotion gate, so its triples are not approved for unchecked bulk weight
influence. `gemma4:12b-mlx` exceeded the Mac's available Metal memory and is not a local fallback.

Run the isolated quality gate before changing a Mem0 model, prompt, or compatibility profile:

```powershell
python -m data_retrieval evaluate-mem0-entities `
  --fixture .\evals\mem0_entity_quality_v1.json `
  --mem0-config .\evals\mem0_entity_smoke_config.json
```

Use repeatable `--case-id` options to retest only failures with a candidate reviewer. The command
writes a detailed report under `data/results/`, returns a non-zero status when a gate fails, and
does not write to the canonical Cortex repository.

## Safe rollout

Start with one namespace and one document:

```powershell
python -m data_retrieval bootstrap-mem0 `
  --postgres-dsn $env:DATA_RETRIEVAL_POSTGRES_DSN `
  --namespace <namespace> `
  --mem0-config .\config\mem0.local.json `
  --max-documents 1
```

Inspect these counters:

- `mem0_records_returned`: normal Mem0 memories produced for diagnostics, not imported by this
  pipeline;
- `entities_returned` and `relationships_returned`: provenance-valid graph objects;
- `relationships_quarantined`: graph relations Cortex rejected because provenance was unsafe;
- `entities_imported`, `entity_support_links_created`, and
  `entity_relationship_links_created`: native projection writes;
- `calibration_signals_created`: immutable replay guards for the new native links.

Run the same command again. It should report resumed batches and zero newly processed batches.
Then raise the cap or select a namespace prefix.

At this point entity relationships are auditable proposals, not active serving edges. For an
isolated test namespace, run `calibrate-mem0-vectors` as documented in
`MEM0-VECTOR-COLD-START.md`. The current policy is experimental and failed its semantic promotion
gate, so do not apply it unattended to a large namespace.

An empty graph result remains retryable by default because a local model or parser failure can
look like a legitimate empty result. After inspection, `--accept-empty` marks empty batches
complete explicitly. A provider failure does not write a completion marker.

Only content and bridge-owned metadata are sent to Mem0. Arbitrary source metadata—including
benchmark answers and evidence labels—is not forwarded. Mem0-produced entity documents are
excluded from later bootstrap runs, preventing recursive ingestion. Documents are processed in
occurrence-time order. Each native namespace receives an isolated Mem0 `user_id`; a custom
`--mem0-user-id` is allowed only with one exact namespace.

## Retrieval behavior

An entity-name lexical hit or source-evidence hit can enter this bounded path:

```text
evidence -> entity mention -> related entity mention -> supporting evidence
```

The path score multiplies support and relationship confidence, is normalized within the current
candidate neighborhood, and is included in retrieval diagnostics. Entity atoms are navigation
nodes; the useful source evidence is what the path is designed to surface. Usage-based weight
learning remains a separate later capability.

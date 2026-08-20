# Mem0 bootstrap runbook

## What runs where

- **Laptop:** the Data Retrieval CLI, canonical PostgreSQL data, Mem0's working Qdrant/SQLite
  files, completion signals, and imported memories.
- **Mac Mini:** Ollama model inference reached through the existing SSH tunnel.
- **Retrieval runtime:** reads only native PostgreSQL objects. It does not call Mem0.

This keeps the Mac replaceable and avoids copying the canonical dataset onto it. Mem0's local
working files are a rebuildable processor cache, not the source of truth.

## Configure Mem0 on the laptop

Install the optional adapter:

```powershell
python -m pip install -e ".[mem0]"
```

Create an uncommitted `config/mem0.local.json`. The current Mac-compatible pair is
`qwen3.5:4b` plus the 1024-dimensional `qwen3-embedding:0.6b`:

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
  "history_db_path": "data/mem0/history.sqlite3",
  "llm": {
    "provider": "ollama",
    "config": {
      "model": "qwen3.5:4b",
      "temperature": 0.1,
      "ollama_base_url": "http://127.0.0.1:11435"
    }
  },
  "embedder": {
    "provider": "ollama",
    "config": {
      "model": "qwen3-embedding:0.6b",
      "ollama_base_url": "http://127.0.0.1:11435"
    }
  }
}
```

The port `11435` is the laptop end of the SSH tunnel. Ollama itself remains on Mac port `11434`.
Do not put passwords, SSH keys, or provider API keys in this file.

## Safe rollout

Start with one namespace and one document:

```powershell
python -m data_retrieval bootstrap-mem0 `
  --postgres-dsn $env:DATA_RETRIEVAL_POSTGRES_DSN `
  --namespace <namespace> `
  --mem0-config .\config\mem0.local.json `
  --max-documents 1
```

Inspect `memories_returned`, `memories_imported`, `source_lineage_links_created`, and
`calibration_signals_created`. Run the same command again: it should report resumed batches and
zero newly processed batches. Then raise the cap or select a namespace prefix.

An empty Mem0 result is reported as an `empty_batch` and remains retryable because some local
models turn malformed extraction output into an empty result. After inspecting a genuinely
unmemorable batch, `--accept-empty` can mark it complete explicitly.

The bridge never sends arbitrary source metadata. This is particularly important for benchmark
data: expected answers and evidence labels stay outside the Mem0 prompt. Mem0 output documents
are also excluded from later runs, preventing recursive generation.

The adapter supplies a Data Retrieval extraction policy by default. Unlike Mem0's personal-memory
default, it retains objective claims, entity roles, events, decisions, state changes, and exact
temporal or numeric details from both sides of a conversation. A config file can override it with
`custom_fact_extraction_prompt`. The Mem0 version, LLM model, and extraction/update prompts are
fingerprinted into completion markers, so changing extraction behavior safely reprocesses source
batches instead of silently reusing stale calibration.

For Ollama, the adapter disables the model's hidden thinking channel during Mem0 calls. These calls
need short JSON rather than a chain of thought; with reasoning models such as Qwen 3.5, leaving the
channel enabled can consume Mem0's output-token budget and produce an empty JSON response.
The boundary also normalizes a common small-model variation where each extracted fact is wrapped
as `{"fact": "..."}` instead of being returned as a plain string.

Documents are processed by occurrence time, not by their hashed IDs. All documents in one native
namespace share one isolated Mem0 `user_id` and omit `run_id`, allowing a later session to be
compared with earlier memories. Different namespaces cannot share this state; a custom
`--mem0-user-id` is therefore allowed only with one exact `--namespace`.

## Failure behavior

If Ollama, the tunnel, Mem0, or PostgreSQL fails, the current batch is not marked complete. Empty
output is also retryable by default. A later run retries that batch. Mem0 may have accepted the remote call before a
laptop failure, but exact/semantic deduplication plus stable native calibration IDs prevent
duplicate native evidence from repeatedly changing weights.

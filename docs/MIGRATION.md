# Migration plan

## Sources

The migration uses the old repositories as references, not as packages or Git
history dependencies:

1. `Tags-Project` contributes the original tag-first retrieval intent.
2. `DebUI/subprojects/data-memory` contributes the atom model, graph direction,
   catalog-first canonicalization, hybrid retrieval, and evaluation ideas.

No credentials, copied corpora, caches, generated reports, or unrelated DevUI
application code belong in this repository.

## Milestones

### 1. Domain foundation — initial slice complete

- Stable document, atom, and tag identity
- Ordered chunks with source offsets
- Tag normalization and provenance
- Atomic repository contract
- Idempotent text ingestion
- Provider-independent raw persistence before enrichment

### 2. Temporal atom projection — initial slice complete

- Timestamped atoms mapped to Temporal History events
- Calendar summaries mapped back into atoms
- Generic atom lineage links
- Deterministic mock-provider integration and resumable state
- Ollama summarizer and stored-atom range projection
- Full structured Temporal summary metadata and lineage preservation
- Conditional current-state, as-of, range, and history retrieval lens
- Next: thread scopes and pressure compaction when real use requires them

### 3. Canonical persistence — initial adapter complete

- SQLite transaction for documents, atoms, tags, weights, and atom lineage
- Foreign-key and uniqueness constraints
- Complete model hydration after close and reopen
- WAL mode and rollback verification
- PostgreSQL/pgvector scale adapter with model-specific vector indexes
- Bounded indexed candidate queries for large namespaces
- Restart-safe staged ingestion for large UTF-8 files
- Neo4j deferred unless measured traversal requirements justify a projection

### 4. Tag generation and canonicalization — in progress

- Provider-neutral atom tag proposal interface
- Ollama adapter verified through an SSH tunnel to the Mac worker
- Strict response validation, provenance, confidence, and atomic enrichment behavior
- Durable provider/version markers for repeatable document enrichment
- Exact catalog match before semantic match
- Configurable semantic threshold
- First-class `catalog_match` and `proposed_new` state
- Deterministic fallback and replayable evidence

### 5. Retrieval

- Direct tag, lexical, and replaceable semantic candidate channels
- Query-aware traversal of learned tag and atom relationships
- Conditional temporal filtering and scoring rather than a flat time score
- Complete per-channel score and evidence breakdown
- Content-hash-aware embedding reuse by provider and model
- Retrieval audit events for downstream feedback attribution
- Next: measured candidate limits and adjacency only if the evaluation corpus needs them

### 6. Learning and evaluation

- Feedback events and bounded, atomic updates
- Learned `HAS_TAG`, tag co-occurrence, and atom `CO_USED` weights
- Reduced source credit when a derived summary is selected
- Honest no-embedding and semantic baselines
- Six-case labeled dataset before advanced group-tag behavior
- Next: grow the dataset from real misses and add negative/outcome calibration

### 7. Calibration and Mem0 — initial slice complete

- Versioned deterministic teacher signals for entity density, code structure, and rarity
- Replay-safe atom-tag priors, tag co-occurrence, hierarchy, and source adjacency
- Mem0 JSON/JSONL bridge with native atoms, 0.92 semantic dedupe, lineage, conflicts, and 2x learning
- Batched calibration backfill without embedding regeneration
- First-class interaction atoms and selected-evidence attribution

See [ADR-0008](decisions/0008-replayable-calibration-and-mem0.md) and the
[context ledger](CONTEXT_LEDGER.md).

## Explicitly deferred

- HTTP API and authentication
- Background scheduler
- Webhooks and dead-letter queues
- Adaptive channel weights
- Group-tag promotion and skill distillation

These features can be added after retrieval quality is measurable. Importing
them earlier would make the core harder to reason about and debug.

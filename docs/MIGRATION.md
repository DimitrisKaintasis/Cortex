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

### 2. Temporal atom projection — in progress

- Timestamped atoms mapped to Temporal History events
- Calendar summaries mapped back into atoms
- Generic atom lineage links
- Deterministic mock-provider integration and resumable state
- Ollama summarizer and stored-atom range projection
- Full structured Temporal summary metadata and lineage preservation
- Next: thread scopes, pressure compaction, and context-frontier retrieval

### 3. Canonical persistence — initial adapter complete

- SQLite transaction for documents, atoms, tags, weights, and atom lineage
- Foreign-key and uniqueness constraints
- Complete model hydration after close and reopen
- WAL mode and rollback verification
- Neo4j deferred until measured graph-query requirements justify a server

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

- Query tag generation
- Direct tag, lexical, and semantic candidate channels
- Query-aware traversal of weighted tag relationships
- Versioned scoring with a complete score breakdown
- Reliable adjacency based on document position

### 6. Learning and evaluation

- Feedback events and bounded updates
- Learned `HAS_TAG` and `RELATED_TO` weights
- Honest direct-tag and semantic baselines
- Small labeled dataset before advanced group-tag behavior

## Explicitly deferred

- HTTP API and authentication
- Background scheduler
- Mem0 integration
- Webhooks and dead-letter queues
- Adaptive channel weights
- Group-tag promotion and skill distillation

These features can be added after retrieval quality is measurable. Importing
them earlier would make the core harder to reason about and debug.

# ADR-0008: Replayable calibration with Mem0 as a first-class provider

## Status

Partially superseded by ADR-0015. Replayable teacher calibration and the legacy Mem0 export
import remain accepted. The reverse-bootstrap fact import and Mem0/vector all-pairs tag
calibration described below are historical and are no longer active.

## Context

The original graph cannot learn useful relationships from an empty state. Earlier revisions
used deterministic teachers to initialize atom importance and Mem0 to bootstrap high-value
memory evidence. During migration, Mem0 was listed as deferred without retaining this dependency,
leaving relationship traversal implemented but unpopulated.

Making Mem0 a second runtime database would split canonical state and make retrieval depend on
an external product's availability and schema. Applying undocumented weight changes would make
large backfills unsafe and impossible to compare.

## Decision

- PostgreSQL/native atoms remain authoritative.
- Deterministic teachers and Mem0 emit immutable `CalibrationSignal` records.
- Signal IDs are stable; replaying a signal cannot apply its weight change twice.
- Calibration profiles preserve provider, confidence, multiplier, target, source reference,
  metadata, and version.
- Mem0 imports become native atoms and may use existing tag and embedding providers.
- Exact duplicates and semantic near-duplicates at 0.92 reuse native atoms while preserving
  record-level Mem0 lineage.
- Mem0 lineage/conflict calibration and the existing outcome-learning path retain the accepted
  2x multiplier. Relationship bootstrap uses the explicit bounded steps below instead.
- Mem0-derived atom-tag and tag co-occurrence proposals now use a joint cold-start profile.
  The Mem0 proposal and embedding corroboration are stored as separate immutable signals.
- Embeddings may add at most half the corresponding Mem0 proposal step. This recognizes their
  same-source correlation and prevents semantic proximity from manufacturing a typed edge.
- Vector corroboration compares the derived fact with its canonical tag labels and, when exact
  source lineage is available, with its supporting source atoms. Missing or failed embeddings
  leave the Mem0-only proposal usable and are reported rather than blocking import.
- The joint profile includes the embedding provider and model. Replaying the same profile is
  idempotent, while a changed profile is distinguishable and auditable.
- Declared conflicts become explicit 0.5-confidence `CONFLICTS_WITH` links instead of silent
  overwrites.
- Mem0 import batches are bounded at 500 records.
- Teacher calibration derives entity-density, code-structure, and relative-rarity evidence,
  then initializes atom-tag priors, co-occurrence relations, hierarchy, and source adjacency.
- Large documents and existing namespaces can be calibrated in bounded, replay-safe batches
  without recomputing embeddings.
- Existing native documents may be processed by the reverse bridge. It uses ordered batches,
  calls Mem0 with inference enabled, imports distilled outputs, and connects each output to its
  source atoms with `DERIVED_FROM` links.
- A stable completion signal is written for every processed source atom. Outputs and completion
  signals are committed before a batch is considered complete, so interrupted jobs resume and
  completed batches never call Mem0 or increase weights twice.
- Only internal source IDs and bridge fields are copied into Mem0 metadata. Source metadata is
  not forwarded, preventing benchmark answers, evidence labels, or provider secrets from being
  used as inference input.
- Mem0-produced documents are excluded from subsequent bridge runs to prevent recursive
  self-ingestion.
- Empty Mem0 output remains retryable by default because local-model parse failures can be
  surfaced by the SDK as an empty result. Operators may explicitly accept an empty batch.
- Source documents are streamed in occurrence-time order. A namespace-scoped Mem0 identity and
  no per-batch `run_id` let later sessions compare against earlier ones without crossing native
  namespace boundaries.

## Consequences

- The graph has useful initial structure before user outcomes accumulate.
- Mem0 can be upgraded, replaced, or unavailable without breaking retrieval.
- Calibration consumes rows proportional to evidence volume, trading storage for auditability.
- Profile changes create new signals rather than mutating history, so backfills stay explicit.
- Semantic deduplication requires an embedding provider; exact deduplication remains free.
- Without an embedding provider, Mem0 still initializes relationships but receives no vector
  corroboration increment.
- The bridge does not make Mem0 a serving dependency: retrieval still works if Mem0 or the Mac
  is offline.

# ADR-0009: One canonical core with replaceable inference processors

- Status: Accepted
- Date: 2026-09-02

## Context

Data Retrieval integrates ideas and executable behavior from the original Tags project, Mem0,
and Temporal History. Each system can perform some form of extraction, organization, temporal
interpretation, and retrieval. Allowing all three to own overlapping serving paths would make
failures difficult to attribute and benchmarks difficult to interpret.

The domain model also overloaded `AtomKind` with both epistemic role and specialized payload
meaning. In particular, Mem0-produced text could be persisted with kind `source`, causing a
derived interpretation to receive raw-evidence treatment.

## Decision

1. Data Retrieval owns canonical identity, source evidence, accepted relationships, immutable
   learning/calibration history, query policy, and final evidence packing.
2. Mem0 is a replaceable fact-extraction and proposal processor.
3. Temporal History is a replaceable calendar-materialization, summary, coverage, and lineage
   processor.
4. Tags owns the canonical conceptual catalog and learned associative address space.
5. Embedding models are replaceable candidate providers.
6. Processors emit versioned derived artifacts and proposals with exact source support; they do
   not mutate canonical source evidence or own final retrieval.
7. Atom evidence role and payload modality become independent canonical fields. Legacy
   `AtomKind` remains temporarily for compatibility and specialized projection behavior.
8. Capabilities are tested independently, then pairwise, then as a complete system.
9. Replacements occur only at narrow, benchmark-supported boundaries.

The detailed authority is `docs/ARCHITECTURE.md`.

## Alternatives

### Federated retrieval across Mem0, Temporal History, and Tags

This exposes more functionality quickly, but creates duplicate candidate fusion, conflicting
time semantics, unclear feedback ownership, and benchmarks that cannot identify the failing
component.

### Reimplement Mem0 and Temporal History inside Tags

This maximizes control but duplicates mature extraction and calendar-integrity work before Tags
has demonstrated unique retrieval value.

### Use Mem0 as the complete memory system

This is operationally simpler but gives up a canonical cross-processor evidence ledger,
explicit outcome-weighted associations, strict source/derived separation, and independent
temporal materialization.

## Consequences

- Adapters become explicit architectural boundaries rather than convenience integrations.
- Raw capture remains operational when inference providers are unavailable.
- Component tests can attribute quality changes to one capability.
- Mem0 and Temporal History can be upgraded or replaced without migrating canonical source
  evidence.
- The core assumes responsibility for evidence packing, traceability, and accepting processor
  proposals.
- A backward-compatible atom schema migration is required before fact-level provenance and
  pairwise evaluation can be trusted.

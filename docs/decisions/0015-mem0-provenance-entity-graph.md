# ADR-0015: Provenance-preserving Mem0 entity graph

## Status

Accepted and implemented. Supersedes ADR-0008 only for the reverse Mem0 bootstrap and its
Mem0/vector all-pairs tag calibration.

## Context

Mem0 was intended to contribute relationships during initial ingestion. The previous bridge
instead imported Mem0 facts, guessed their source support after extraction, inherited evidence
tags, and boosted every resulting tag pair. That loses Mem0's actual entity relationships and
can create false shared associations. A globally shared proper-name tag is especially unsafe:
two unrelated users can mention different people named Alice.

Mem0 1.x already extracts and reconciles entities, but its graph relationship result does not
identify which input atom supports each endpoint. Matching entities back to atoms afterward
would require ambiguous lexical, vector, or model-based alignment.

## Decision

- Keep standard self-hosted `Memory.add(infer=True)` behavior, including Mem0's fact memory and
  entity graph.
- Do not edit the installed Mem0 package or fork its algorithms.
- Wrap one Memory instance with an opt-in relationship-extraction extension. Entity extraction
  receives plain text; relationship extraction also receives opaque source-atom markers and a
  tool schema requiring relationship and endpoint evidence IDs.
- Strip provenance fields before Mem0 stores its normal graph triples.
- Validate every returned evidence ID against the exact request. Quarantine relationships with
  missing, malformed, or invented provenance; never repair them through post-hoc matching.
- Import each accepted Mem0 entity as a private, batch-scoped, derived entity-mention atom.
- Connect entity mentions to exact source atoms with `SUPPORTED_BY` and to one another with
  `MEM0_ENTITY_RELATION`. Keep the Mem0 predicate in link metadata.
- Copy no source tags onto entity atoms. Retrieval traverses bounded
  evidence/entity/relationship/evidence paths instead.
- Store stable calibration signals for replay protection, not for hidden all-pairs tag boosts.
- Keep PostgreSQL authoritative. Mem0's Qdrant, history database, and Kuzu graph are rebuildable
  working state and are not serving dependencies.
- Pin the integration to Mem0 major version 1 and fail clearly when the private graph methods
  required by the narrow adapter are incompatible.

## Consequences

- Mem0 keeps its tested core functionality and can still improve its own memory state.
- Cortex gains exact entity lineage and useful graph traversal without turning names into global
  concepts or manufacturing tag correlations.
- The model performs provenance attribution in the existing relationship call, avoiding another
  model or embedding pass.
- Invalid provenance can reduce imported graph recall, but it cannot silently contaminate the
  canonical graph.
- Entity identity is deliberately local to an extraction batch. Cross-batch entity resolution is
  a separate measured capability and must not be inferred by this adapter.
- Usage-based weight updates and collective/global aggregation remain separate future layers.

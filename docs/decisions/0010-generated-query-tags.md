# ADR-0010: Generated query tags are an optional first-class retrieval stage

- Status: Accepted
- Date: 2026-09-02

## Context

The original Tags project entered memory through semantic concepts: generate query tags, map
them to stable stored concepts, and use the tag-to-atom inverted index. The current
`RetrievalService` retains a replaceable `TagProposer` boundary, catalog canonicalization, and
graceful degradation, but the normal CLI did not supply a proposer. Most tests supplied
`query_tags` explicitly, so they proved the tag channel without proving the original entry
path.

Making an inference provider mandatory would violate raw/provider independence and would make
ordinary retrieval fail whenever the Mac or model is unavailable.

## Decision

1. Generated query tags are an optional normal retrieval stage, not benchmark-only behavior.
2. Explicit query tags remain supported and are merged with generated tags.
3. Generated tags are normalized and, when an embedding canonicalizer is available, matched to
   the existing tag catalog before retrieval.
4. Provider or canonicalizer failure degrades explicitly to lexical, semantic, and any explicit
   tag channels; it does not fail retrieval.
5. Retrieval diagnostics record the final query tags and degradation warning.
6. The CLI activates Ollama query-tag generation only when `--tag-model` or
   `OLLAMA_TAG_MODEL` is configured.
7. An isolated gate must prove natural-language query -> generated concept -> canonical tag ->
   correct atom without relying on lexical or atom-semantic overlap.
8. This ADR does not decide proposed-tag promotion, hierarchy generation, or recursive graph
   traversal. Those require the richer tag lifecycle described in the architecture
   reconciliation.

## Alternatives

### Require callers to supply query tags

This keeps the runtime deterministic but moves the project-defining semantic entry behavior to
every caller and allows normal integrations to silently omit it.

### Make query-tag inference mandatory

This maximizes tag use but creates a live serving dependency on an inference provider and
breaks graceful degradation.

### Use only whole-query embeddings

This is simpler but turns the tag graph into secondary metadata and loses stable conceptual
routing, hierarchy, and learned tag relationships.

## Consequences

- The normal retrieval path can exercise the original tag-first idea.
- The Mac remains optional; lexical and semantic retrieval still work without it.
- Query-tag model cost and latency become visible serving tradeoffs and must be measured.
- Proposed-tag lifecycle and promotion remain a prerequisite before the graph can safely evolve
  its canonical vocabulary automatically.


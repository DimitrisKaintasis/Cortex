# ADR-0011: Novel AI tags are quarantined candidates

## Status

Accepted and implemented on 2026-09-02.

## Context

Before this decision, a novel model suggestion was stored as a `Tag` with state
`proposed_new` and immediately attached to an atom. Its state looked provisional, but the edge
could participate in retrieval, calibration, and learning. User-supplied tags and model
suggestions also used the same state, and broad/specific level was guessed from whitespace.

## Decision

1. The serving `tags` catalog contains canonical tags only.
2. User-supplied explicit tags are trusted canonical tags with `explicit` edge provenance.
3. Every model output includes text, confidence, and an explicit broad/specific level.
4. Every atom-specific output is persisted as a `TagCandidate` with producer and proposal
   version.
5. Exact catalog matches are immediately `canonicalized`; semantic catalog matches are
   immediately `merged`. Both may create an edge to the existing canonical tag.
6. A novel output remains `proposed`. It creates neither a `Tag` nor an `AtomTag`, so retrieval,
   calibration, graph expansion, and learning cannot observe it.
7. Review atomically promotes a candidate, merges it into an existing canonical tag, or rejects
   it with a reason. Resolved candidates cannot be resolved again.
8. SQLite and PostgreSQL migrate historical rows by provenance: edges containing `explicit`
   become canonical; remaining `proposed_new` tags become quarantined legacy candidates.

## Consequences

- Catalog growth is deliberate and auditable.
- Provider/model versions remain attributable after review.
- New model vocabulary cannot silently change serving behavior.
- Large unattended ingestion may accumulate candidates that require a later review policy.
- Automatic promotion thresholds remain a future evaluation decision, not a hidden default.

## Alternatives rejected

### Serve provisional exact matches

This preserves recall but still lets unreviewed vocabulary affect normal behavior and weights.

### Automatically promote every novel proposal

This recreates the old catalog-contamination problem under a different name.

### Require manual review for catalog matches

Exact and high-threshold semantic matches already resolve to an accepted concept, so requiring
review would add cost without protecting the vocabulary boundary.

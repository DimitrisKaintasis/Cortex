# ADR-0006: Staged retrieval and outcome-based learning

- Status: Accepted
- Date: 2026-08-20

## Context

The original Tags project treated weighted atom-tag relationships as the main retrieval
signal. The later Cortex revision added lexical, semantic, recency, graph, and feedback
ideas, but some specifications were ahead of the runtime behavior. Temporal History adds
calendar summaries and time-aware search, but time should not distort every ordinary
query.

Automatically reinforcing every returned result would create a feedback loop: the
system would strengthen its own guesses without evidence that a person or downstream
task found them useful. Updating factual lineage through behavioral feedback would also
mix two different meanings in one edge type.

## Decision

Use one staged retrieval pipeline:

1. direct tags, lexical overlap, and optional stored embeddings generate candidates;
2. learned tag co-occurrence and atom `CO_USED` links expand them;
3. a conditional temporal lens resolves `none`, `current_state`, `as_of`, `range`, or
   `history` semantics;
4. redundant summaries are removed from the final evidence pack;
5. every returned result and score is recorded under a unique retrieval ID.

Keep structural and learned relationships separate:

- `SUMMARIZES`, `DERIVED_FROM`, and `SUPERSEDES` are factual/provenance links;
- `CO_USED` and tag `co_occurs` are learned behavioral links.

Apply learning only after explicit positive or negative outcome feedback. Updates are
small, bounded, atomic, and tied to a unique feedback ID. A directly selected source
gets full credit. Selecting a derived summary sends reduced credit through its lineage
to source atoms. Replaying the same feedback ID is rejected.

Embedding models are runtime configuration. Stored vectors are keyed by atom, provider,
model, and source content hash. A model change therefore requires new derived vectors,
not a schema or source-data migration.

## Alternatives

### Add time as another global score

This is simpler numerically, but it lets freshness override explicit as-of questions and
penalizes timeless knowledge even when time is irrelevant.

### Learn from every retrieved item

This creates self-confirming weights and makes it difficult to distinguish model bias
from real user preference.

### Put sequence/order into the atom graph now

This overloads associative relationships with procedural meaning. Ordered problem-
solving steps remain a later structure built from retrieved evidence.

### Pin one embedding model in source code

This makes experiments and upgrades expensive. Runtime selection with versioned stored
vectors keeps the boundary replaceable.

## Consequences

- Retrieval remains explainable across relevance, relationship, and temporal stages.
- Factual evidence and behavioral learning cannot silently overwrite each other.
- Feedback requires a recorded retrieval and selected returned atoms.
- SQLite owns canonical atoms, vectors, events, and learned weights on the laptop.
- The Mac performs inference through the SSH tunnel and stores only model files.
- Model comparisons must use the same checked-in evaluation corpus.

# Evidence packing policy

Retrieval ranking and evidence packing are deliberately separate stages. Ranking estimates how
relevant each eligible atom is. Packing decides which ranked atoms may share the final bounded
context without hiding provenance or allowing one representation type to crowd out the others.

## Default policy

- Reserve 40% of `top_k` for the highest-ranked source-role atoms when source candidates exist.
- Cap derived-role atoms at 50% of `top_k` when source candidates exist.
- Allow derived atoms to fill the pack when no source candidate is available.
- Collapse derived atoms with the same non-empty direct lineage set.
- Collapse derived atoms whose normalized token-set Jaccard similarity is at least 0.90.
- Preserve relevance order among the items that survive packing.

An evidence pack may intentionally contain fewer than `top_k` items. This happens when filling
the remaining slots would violate the derived cap or add only duplicate representations. The
system reports this as `packing.underfilled`; it does not silently relax the policy.

These are initial safety defaults, not universal constants. They must be benchmarked under fixed
candidate sets and token budgets before changing them. Applications can inject a different
`EvidencePacker` policy without changing candidate scoring.

## Labels

Every retrieval item exposes two independent labels:

- `atom_role`: `source`, `derived`, `interaction`, or `uncertainty`;
- `temporal_label`: `none`, `current`, `historical`, or `continuity`.

The compatibility field `role` contains the temporal label when one applies and otherwise the
atom role. `AS_OF` results are historical relative to the current query even when an item was the
latest state at the requested cutoff. Temporal summaries remain continuity evidence.

## Diagnostics

Every retrieval event stores the complete packing policy and:

- source candidates, target, and selected count;
- derived limit;
- selected counts by atom role;
- exclusion counts for `derived_cap`, `lineage_equivalent`, and `near_duplicate`;
- whether the pack was intentionally underfilled.

This makes packing changes replayable and lets isolated benchmarks distinguish ranking failures
from context-composition failures.

## Rejected alternatives

Returning the top scores without constraints is simpler but allows summaries or extracted facts
to occupy the complete context. Requiring raw evidence for every result is safer but prevents
useful retrieval when only imported or derived memory exists. A model-based final packer could be
more flexible, but it would add cost, nondeterminism, and another failure boundary before the
deterministic policy has been benchmarked.

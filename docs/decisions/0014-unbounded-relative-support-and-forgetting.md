# ADR-0014: Unbounded support, relative influence, and recoverable forgetting

## Status

Accepted as future-scope architecture on 2026-09-02. The immutable event prerequisite is
implemented; collective support, decay, and normalization are not.

## Context

The first Tags design bounded weights to `[0,1]`, reinforced verified useful use by an example
`+0.05`, and subtracted an example `0.02` from unused relationships every `N` interaction ticks.
The later Cortex recovery deliberately changed `HAS_TAG`, `RELATED_TO`, and `CONTAINS` raw
weights to non-negative unbounded values and used relative `log1p` scoring. That revision did
not consistently adapt passive decay, negative learning, groups, and every retrieval path.

## Decision

1. Positive and negative learned support are separate, non-negative, unbounded accumulators.
2. Confidence, reliability, cohesion, probability-like values, and final serving influence
   remain bounded.
3. Verified positive and negative outcomes append attributable observations; retrieval or
   co-occurrence alone never reinforces or penalizes an edge.
4. Passive decay applies only to behavioral attention and is evaluated lazily. It never scans
   and rewrites the whole graph or deletes provenance/history.
5. The first decay candidate is proportional half-life decay. Exact half-lives, maturity rules,
   and negative penalties are versioned policy parameters selected by ablation.
6. Net support is converted to bounded influence through relative `log1p` normalization inside
   an explicit relation/scope/query neighborhood. Arbitrary unbounded raw values are never
   multiplied across a traversal path.
7. Relationships may become hot, warm, cold, dormant, inhibited, or reactivated. Dormancy
   removes serving influence, not event history.
8. Individual-user contributions are bounded; collective magnitude must reflect independent
   evidence rather than one contributor's event volume.
9. Policy and serving snapshots are versioned. The graph is not copied or manually versioned
   edge by edge.

## Consequences

- Mature relationships may accumulate support in the hundreds or beyond without saturating.
- Diminishing-return normalization prevents magnitude from directly dominating retrieval.
- Fixed subtraction such as `-0.02` is retained as a historical baseline but is not scale-safe
  for unbounded support.
- Known harmful evidence remains distinguishable from an unknown zero-strength relationship.
- Existing before/after weight events remain useful history but require observation and support
  projections for the collective design.
- Current cap-at-10 and raw-multiplication code paths are implementation mismatches.

## Rejected alternatives

### Bound every learned relationship to `[0,1]`

This loses the difference between modest and repeatedly independent evidence once both saturate.

### Use unbounded raw weights directly in ranking and traversal

This makes traffic volume and outliers dominate unrelated components and scopes.

### Store one signed scalar only

This makes unknown, weak, contradicted, and actively harmful states difficult to distinguish and
audit.

### Periodically decrement every edge

This cannot scale to the intended graph size and creates continuous writes for inactive state.

## Authority

The equations, comparison boundaries, historical baselines, and experiments are specified in
`docs/COLLECTIVE-CAPABILITY-GRAPH.md`.

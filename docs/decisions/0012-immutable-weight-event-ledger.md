# ADR-0012: Serving weights are event-backed aggregates

## Status

Accepted and implemented on 2026-09-02.

## Context

Calibration signals were immutable, and feedback events were stored, but atom-tag, atom-link,
and tag-relation rows still held mutable weights without one replayable history. This prevented
reliable explanation, aggregate reconstruction, and safe higher-order learning.

## Decision

1. `weight_events` is the append-only authority for every weighted edge transition.
2. Each event records the target coordinates, source and source ID, policy version, before and
   after weights, delta, timestamp, and metadata.
3. Current edge `weight_raw` remains a serving aggregate for fast retrieval.
4. Ingestion, calibration/Mem0, explicit feedback, and tag review append events in the same
   transaction as their aggregate changes.
5. Event IDs are deterministic and replays cannot duplicate logical transitions.
6. Pre-ledger databases receive one explicit `migration` baseline per existing edge. This
   reconstructs the starting state but truthfully marks detailed earlier history unavailable.
7. Auditing reconstructs weights from event chains and compares them with serving aggregates.
8. Aggregate repair is explicit and changes only the cache; it never manufactures or rewrites
   history.
9. PostgreSQL serializes runtime weight mutations with a transaction-scoped namespace advisory
   lock. Maintenance repair runs with namespace workers stopped.

## Consequences

- Every future weight change has attributable origin and policy identity.
- Serving remains fast because retrieval does not replay events on each query.
- Corrupted or stale aggregate weights can be detected and rebuilt.
- Historical changes made before this ledger cannot be recovered individually; they begin from
  a labeled migration snapshot.
- Deleting or rewriting weight events is outside normal application behavior.

## Alternatives rejected

### Replay calibration and feedback tables independently

This leaves ingestion baselines, tag review, and new relationship types outside one contract.

### Calculate weights from all events during retrieval

This would make the audit model simple but impose unnecessary latency on every query.

### Store only deltas

Before/after values make broken chains and aggregate divergence directly detectable.

# ADR-0002: Temporal History as an atom projection

- Status: Accepted
- Date: 2026-08-19

## Context

Temporal History already provides tested calendar-aware summarization, lineage,
coverage, resumable generation, pressure compaction, and context-frontier logic.
Its full pipeline is coupled to one export schema and filesystem artifacts, while
Data Retrieval treats atoms and tags as its canonical domain.

Copying its temporal concepts into separate Day, Week, Task, Decision, and Summary
entities would work against the intentionally small atom architecture. Running it
as an independent canonical store would also introduce synchronization and data
ownership problems.

## Decision

1. Pin Temporal History to an exact Git commit until it publishes a stable release.
2. Keep all imports from its alpha API inside one `TemporalBridge` adapter.
3. Convert timestamped source atoms to Temporal History normalized events.
4. Convert generated temporal summaries back into atoms.
5. Represent source and child-summary lineage with one generic `AtomLink` type.
6. Keep Temporal History's SQLite state as a rebuildable generation cache; the
   Data Retrieval repository remains canonical.

Calendar resolution, model identity, coverage, decisions, actions, and similar
properties are metadata on summary atoms. They do not become new domain entities.

## Alternatives

### Run Temporal History as a separate service

This would preserve its current CLI unchanged, but would create a second source of
truth and add networking and deployment work before retrieval quality is measured.

### Copy or fork the implementation

This avoids relying on an alpha internal API, but immediately creates maintenance
drift from an actively changing upstream project.

### Reimplement temporal summarization

This gives complete control but duplicates working, tested calendar and lineage
logic without producing user value.

## Consequences

- The bridge protects the rest of the codebase from upstream API changes.
- Every temporal claim remains traceable to canonical source atoms.
- Untimed atoms can still be tagged and retrieved, but cannot enter a temporal
  projection until an occurrence time is supplied.
- Thread projection, pressure compaction, context-frontier retrieval, and the Mac
  model provider can be added through the same boundary.

# ADR-0001: Focused tag core with one canonical store

- Status: Superseded by ADR-0003 for the initial canonical store
- Date: 2026-08-19

## Context

The original prototype split canonical data between MongoDB and Pinecone. The
later Cortex implementation introduced a richer graph model, but also bundled
the tag system with API, scheduler, personalization, and operational features.
It contains known gaps between its documented graph semantics and its Neo4j
read path.

## Decision

Build a focused successor with:

1. Neo4j as the eventual single canonical data store.
2. A repository protocol that keeps domain logic independent of Neo4j.
3. An in-memory repository used by deterministic unit tests.
4. Stable content-derived identifiers and explicit source ordering.
5. Provider interfaces for future LLM and embedding integrations.
6. A library and CLI before any network API.

## Alternatives

### Repair the 2024 prototype

Lower initial effort, but retains cross-database consistency risks and unclear
data ownership.

### Copy Cortex wholesale

Provides more features immediately, but imports unrelated infrastructure and
known tag-hydration and scoring problems.

## Consequences

- External-service integration is delayed until the domain model is tested.
- Features must earn their complexity through retrieval evaluation.
- Migration is selective, so old behavior is not automatically compatible.
- Storage adapters must persist and hydrate the complete domain contract.

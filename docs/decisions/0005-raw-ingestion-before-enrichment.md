# ADR-0005: Persist raw ingestion before optional enrichment

- Status: Accepted
- Date: 2026-08-19

## Context

The first Ollama integration called the model while constructing the ingestion bundle.
That made the Mac, SSH tunnel, and model output part of the availability boundary for raw
data. A provider failure prevented otherwise valid source content from being stored.

Temporal History also produces derived interpretation rather than canonical source
evidence. Treating either operation as raw ingestion would blur provenance and make
recovery harder.

## Decision

Use two explicit stages:

1. Raw ingestion deterministically stores the document, ordered source atoms, source
   metadata, and user-supplied tags in one transaction.
2. Optional enrichment reads stored atoms and atomically adds either model tag proposals
   or Temporal summary atoms and lineage.

Tag enrichment records a stable provider and prompt-version marker in document metadata.
Repeating a completed version is a no-op, including when it proposed no tags. A failed
provider call writes no partial enrichment and leaves the raw document untouched.

Temporal enrichment selects timestamped source atoms by namespace, timeline, and
half-open range. Temporal History state provides period-level reuse; summary records are
stored as derived atoms with full structured metadata and source/child lineage.

Temporal topics are not automatically promoted to tags because they currently lack the
confidence and normalization needed by the tag graph.

## Alternatives

### Keep enrichment inside ingestion

This has one command but couples durable capture to inference availability and makes
retry semantics ambiguous.

### Add a durable job queue now

This would support background execution, but it adds worker lifecycle, leases, retry
policy, and another operational store before hosted storage is chosen.

### Store partial model output per atom

This improves progress on long documents but exposes incomplete enrichment states. The
current document-level atomic pass is simpler and adequate for the first implementation.

## Consequences

- Canonical capture works with the Mac and tunnel offline.
- Enrichment can be retried independently and audited by provider/version.
- The CLI has separate `ingest`, `enrich-tags`, and `enrich-temporal` commands.
- The laptop must remain on during work because no background worker exists yet.
- Long enrichment passes restart at the document or Temporal-period boundary after a
  failure; finer-grained jobs can be added when hosted storage is selected.

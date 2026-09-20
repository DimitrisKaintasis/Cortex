# ADR-0021: Source-owned connector packaging

- Status: Accepted
- Date: 2026-09-20
- Clarifies: ADR-0020

## Context

ADR-0020 correctly placed connector-specific mapping outside the Cortex core. C5 and C6 then used
DevUI-like and Slack-like implementations to pressure-test the public contract. Packaging those
reference implementations as installable Cortex commands made the ownership boundary ambiguous:
it suggested that Cortex should release and operate source-specific integrations.

That implication conflicts with the intended product model. Each external application owns its
API credentials, source schema, rate limits, cursor acquisition, mapping policy, and source UI.
Cortex owns the generic synchronization, retrieval, evidence, and outcome protocol.

## Decision

1. The Cortex distribution ships the source-neutral REST contract, Python SDK, MCP adapter,
   contract-test kit, and generic synchronization helpers.
2. Operational connectors are packaged in the source application's repository or in a separate
   connector repository owned by that integration.
3. Cortex may retain source-shaped fixtures and conformance tests when they use only public generic
   contracts and protect compatibility.
4. Cortex does not ship DevUI-, Slack-, GitHub-, CRM-, or other product-specific commands,
   credentials, polling loops, mappers, or release dependencies by default.
5. A connector may be promoted into an officially maintained separate project later, but that is
   a product/ownership decision rather than a change to the Cortex core API.
6. Shared connector code is extracted into the SDK only after multiple integrations prove the same
   source-neutral responsibility. The accepted example is `sync_source_batches`; source mapping
   and lifecycle interpretation remain external.

## Consequences

- Cortex's compatibility claim is expressed through a stable generic boundary rather than a list
  of built-in integrations.
- Source teams can release connector changes with their own application schemas and credentials.
- The Cortex repository retains regression evidence without taking operational ownership of those
  sources.
- Installing Cortex does not add unrelated source-specific commands or optional extras.
- End-to-end testing of a real connector belongs in both its repository and a compatible Cortex
  test environment.

## Alternatives

### Maintain all connectors in the Cortex monorepo

This simplifies early demos but couples Cortex releases to upstream APIs, credentials, rate-limit
behavior, and source-specific dependencies. It does not scale to the stated any-program boundary.

### Remove all source-shaped evidence from Cortex

This creates a clean repository but weakens compatibility regression coverage. Generic fixtures
that stress unlike source lifecycles are valuable even when operational code lives elsewhere.

### Generate connector packages from a large framework

Two validation sources justified one small orchestration helper, not a framework. A generator can
be reconsidered only after external connector repositories reveal stable repeated structure.

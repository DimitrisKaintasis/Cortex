# ADR-0020: External connector and agent integration boundary

- Status: Accepted for implementation planning
- Date: 2026-09-18
- Refines: ADR-0005, ADR-0009, ADR-0017, ADR-0018, and ADR-0019
- Packaging clarified by: ADR-0021

## Context

Cortex is intended to serve applications and reasoning systems without requiring those consumers
to understand atoms, tag weights, processor lifecycles, repository adapters, or retrieval fusion.
The current loopback API proves ingestion, explainable retrieval, tag review, and attributable
feedback, but it is a local application transport rather than a complete connector contract.

Real integrations introduce lifecycle requirements that one-shot text ingestion does not cover:

- stable identities from an external system;
- incremental and replay-safe synchronization;
- edits, version changes, deletions, and retention decisions;
- explicit source relationships;
- source and connector version provenance;
- organization, project, user, and visibility scope;
- mapping retrieved evidence back to the originating application;
- reporting which evidence actually contributed to an outcome; and
- optional notification of completed processing or review work.

DevUI and Slack are useful reference integrations because they stress different parts of the same
boundary. DevUI supplies structured code and architecture data, while Slack supplies mutable,
threaded, temporal conversation data. Neither application may become a special case in the Cortex
core.

## Decision

1. Cortex exposes one application-facing connector contract. An integration may implement any
   combination of four capabilities:
   - **source sync**: external application to Cortex;
   - **context retrieval**: Cortex to an application or agent;
   - **outcome reporting**: application or agent to Cortex learning;
   - **change observation**: optional Cortex status/events to an application.
2. The public connector vocabulary is `Source`, `Record`, `Relation`, `SyncRun`, `Scope`, `Query`,
   `EvidenceResult`, `ContextPack`, `Outcome`, and optional `Subscription`. Public clients do not
   create atom IDs, tag IDs, weights, calibration events, or storage rows.
3. REST/OpenAPI is the canonical software-to-software contract. A small Python SDK provides the
   reference developer experience. MCP is an agent-facing adapter over the same application
   services, not a second source of domain behavior and not the bulk synchronization protocol.
4. A source record has a stable identity within a source instance. At minimum the connector
   supplies `source_system`, `source_instance`, `external_id`, payload or payload reference,
   occurrence time when known, scope, visibility, and connector version. Optional metadata and
   typed relations remain source-owned inputs subject to Cortex validation.
5. Replaying the same record version is idempotent. Changed source content creates a new canonical
   version linked to the preceding version rather than silently rewriting evidence. Removal uses
   an owner-authorized tombstone or erasure workflow; a connector never deletes repository rows
   directly.
6. Incremental synchronization is represented explicitly. A sync run has a deterministic request
   identity, bounded batches, connector cursor/checkpoint, completion state, and resumable failure
   behavior. A cursor is committed only after the corresponding records and relations are durable.
7. Retrieval returns Cortex evidence identity together with the permitted external identity,
   source provenance, lineage, and explanation required for the caller to map results back into
   its own interface. Empty/abstaining context remains valid.
8. Outcome reporting names a prior retrieval and only evidence returned by that retrieval. An
   outcome may not directly manipulate weights or claim credit for arbitrary records.
9. Namespace remains an organizational and retrieval field, not an authorization boundary.
   Remote calls derive the effective principal and allowed scopes from verified credentials;
   callers do not grant themselves visibility by submitting a namespace or scope in a payload.
10. Connector credentials for upstream systems belong to the connector or an external secret
    manager. Cortex receives normalized records and source provenance, not reusable Slack, GitHub,
    DevUI, or database credentials unless a separately accepted delegated-auth design requires it.
11. Local integration is implemented and tested before public hosting. Stdio MCP and the existing
    loopback API may operate under the local-user trust boundary. Remote HTTP/MCP remains gated on
    authenticated identity, authorization, TLS, rate limits, audit, retention/deletion,
    migrations, backups, and an accepted always-reachable deployment.
12. Structured-project and mutable-conversation fixtures are the first conformance probes.
    Connector-specific mapping and operational packaging stay outside the Cortex core and are
    owned by the source application or a separate connector repository.

## Boundary between connectors and processors

A connector describes what an external source says and how that source changes. A processor
interprets already captured canonical evidence and emits versioned derived artifacts or proposals.

```text
external application
    -> connector mapping and sync
        -> canonical source records
            -> optional Cortex processors
                -> retrieval/context pack
                    -> external agent or application
                        -> attributable outcome
```

A connector may submit source-owned factual relations, such as `reply_to`, `imports`, or
`belongs_to`. It may not submit learned weights or mark model interpretations as canonical source
truth. Processor contracts and proposal review continue to govern inferred tags, entities,
summaries, and relationships.

## Consequences

- An integrator needs to understand their own source model and the connector vocabulary, not the
  Cortex storage or ranking implementation.
- REST, SDK, and MCP calls converge on the same services and policy checks.
- External identity and lifecycle state become canonical schema concerns and therefore depend on
  the versioned migration work in ADR-0019.
- Unlike source-shaped fixtures expose contract weaknesses without making their operational
  integrations Cortex runtime responsibilities.
- A hosted MCP server is not a transport-only feature; it depends on the multi-scope security and
  operational boundary.
- The current `/v1/documents`, `/v1/retrievals`, and `/v1/feedback` endpoints remain valid local
  capabilities while the broader contract is introduced compatibly.

## Alternatives

### Make MCP the only public interface

MCP provides useful tool discovery for agents, but bulk synchronization, deterministic application
integration, webhooks, and ordinary scripts still need a software API. Making MCP authoritative
would couple the domain contract to one agent protocol.

### Let integrations write atoms or database rows directly

This appears flexible but leaks identity, chunking, evidence-role, migration, and learning
internals. It would also bypass validation and make schema evolution unsafe.

### Build one endpoint per source application

Slack-, DevUI-, Git-, and CRM-specific endpoints are initially convenient but move mapping logic
into the core and prevent the data-agnostic claim from being tested.

### Design a complete connector framework before real connectors

This risks encoding hypothetical abstractions. Cortex first exposes the smallest stable contract
and SDK, exercises unlike source-shaped conformance cases, and extracts only proven
source-neutral helpers. Operational integrations remain source-owned under ADR-0021.

## Implementation boundary

This ADR accepts the boundary, terminology, and ordering. It does not make the current loopback API
public, add authentication, select a hosting provider, or authorize schema changes by itself. The
phased implementation and gates are defined in `docs/CONNECTOR-API-PLAN.md`.

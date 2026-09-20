# Cortex connector and agent API plan

- Status: C2 and C3 passed
- Date: 2026-09-20
- Governing decision: [ADR-0020](decisions/0020-external-connector-and-agent-boundary.md)
- Current transport: [loopback-only local API](LOCAL-API.md)

## Objective

Make Cortex straightforward to connect to an arbitrary application without requiring the
connector author to understand Cortex internals.

The public promise is:

> If an integrator can map application data into records, relations, scopes, and outcomes, they
> can use Cortex through a stable contract without knowing how Cortex stores or ranks evidence.

The first proof is not the number of supported products. It is whether two deliberately different
connectors--DevUI and Slack--can use the same contract without source-specific changes to the core.

## Product boundary

```text
DATA SOURCES                                      CONSUMERS

DevUI connector ----\                         /-- IDE agent
Slack connector ------> connector API -> Cortex <-- Slack agent
Git connector -------/          |              \-- ordinary application
                                 |
                    REST/OpenAPI application contract
                                 |
                     Python SDK and MCP adapter
```

Cortex is not an agent framework and not a realtime collaboration bus. Source applications remain
authoritative for their own objects. Cortex preserves, organizes, retrieves, explains, and learns
from the evidence that connectors are permitted to provide.

## Connector capability profiles

A connector declares only the capabilities it implements.

| Capability | Direction | Minimum responsibility |
|---|---|---|
| Source sync | Application -> Cortex | Normalize records, stable external IDs, scope, versions, changes, and deletions |
| Context retrieval | Cortex -> application | Submit queries and consume traceable evidence/context packs |
| Outcome reporting | Application -> Cortex | Attribute success or failure to evidence returned by a prior retrieval |
| Change observation | Cortex -> application | Receive bounded job, review, or memory-status events without treating Cortex as the source application's sync bus |

Common profiles include ingest-only importers, read-only agent tools, retrieval-plus-outcome agent
clients, and full bidirectional application integrations.

## Public vocabulary

| Object | Meaning | Required properties |
|---|---|---|
| `Source` | One configured origin of external evidence | source system, source instance, connector identity/version, owner |
| `Record` | One externally identifiable evidence object or version | external ID, payload/reference, modality, scope, visibility, version |
| `Relation` | Source-owned typed connection between records | source external ID, target external ID, type, confidence/provenance if applicable |
| `SyncRun` | Retryable incremental synchronization boundary | request ID, source, mode, starting cursor, proposed cursor |
| `SyncBatch` | Independently retryable bounded write | stable batch ID, zero-based sequence, run identity, records/relations/tombstones |
| `SyncBatchAcknowledgement` | Durable result for one batch | matching run/batch/sequence, accepted counts, item-specific failures |
| `SyncCommitAcknowledgement` | Proof that the source cursor advanced | commit request ID, run/source identity, committed cursor and time |
| `Scope` | Ownership and access location | organization/project/user/private dimensions independent of namespace |
| `Query` | Request for relevant evidence | text/structured intent, allowed scope, filters, temporal mode, budget |
| `EvidenceResult` | One traceable retrieval item | evidence ID, permitted external identity, content/reference, lineage, scores |
| `ContextPack` | Bounded consumer-ready selection | retrieval ID, items, token/size accounting, abstention/diagnostics |
| `Outcome` | Attributable task result | retrieval ID, used evidence IDs, positive/negative result, reason |
| `Subscription` | Optional outbound notification registration | event types, destination, owner, delivery/retry policy |

The SDK may use friendlier names, but these semantics must remain transport-independent.

## Record envelope

The first contract fixture should cover this logical shape:

```json
{
  "source_system": "slack",
  "source_instance": "workspace:T123",
  "external_id": "channel:C456:message:1720000000.0001",
  "external_version": "edited:1720000300",
  "modality": "text",
  "payload": {
    "inline": "Move refresh-token storage to PostgreSQL."
  },
  "occurred_at": "2026-09-18T12:00:00Z",
  "observed_at": "2026-09-18T12:05:00Z",
  "scope": {
    "organization_id": "org-1",
    "project_id": "project-auth",
    "visibility": "project"
  },
  "metadata": {
    "channel_id": "C456",
    "thread_id": "1720000000.0001"
  },
  "relations": [
    {
      "type": "reply_to",
      "target_external_id": "channel:C456:message:1719999900.0001"
    }
  ]
}
```

Required constraints:

- `(source instance, external ID, external version)` is replay-idempotent within its owner scope.
- A content-changing update creates a new canonical version and a `SUPERSEDES` lineage link.
- Relations may arrive after their endpoint records and are retry-safe.
- Unknown metadata is preserved only within size, type, privacy, and allowlist policy. Contract
  metadata accepts JSON-compatible values only and is recursively immutable after validation.
- Payload references require immutable identity/hash rules before external blobs are supported.
- Connector-supplied tags are explicit source annotations; inferred tags continue through the
  proposal/review lifecycle.

## Synchronization lifecycle

```text
register/resolve source
        -> begin sync(run_id, previous_cursor)
            -> submit bounded batch(batch_id, sequence)
            -> receive durable batch acknowledgement and item failures
            -> retry or repair failures without advancing the cursor
        -> commit sync(new_cursor)
            -> receive commit acknowledgement
```

Rules:

1. Starting or replaying a sync with the same request identity is safe.
2. Batches are bounded and independently acknowledged by stable `(run request ID, batch ID,
   sequence)` identity.
3. Partial failures identify individual records without advancing the committed cursor.
4. Connector cursors are opaque to Cortex and meaningful only to their source connector.
5. A crash after durable ingestion but before commit can replay without duplicates.
6. Tombstoning removes evidence from normal serving after authorization and policy checks.
7. Legal/privacy erasure is distinct from a source-system deletion and receives a separate audit
   and propagation policy.
8. Optional enrichment runs after canonical source capture; model failure never invalidates the
   committed sync.

Batch acknowledgement and cursor commit are deliberately separate. A successful batch means its
accepted items are durable and safe to replay; only a commit acknowledgement proves that Cortex
advanced the source cursor.

## Query temporal contract

Temporal requests carry their boundaries explicitly:

- `as_of` mode requires a timezone-aware `as_of` timestamp;
- `range` mode requires timezone-aware `range_start` and `range_end`, with start before end;
- `current_state` may include `as_of` to ask for the state known at that time;
- range bounds are invalid outside `range` mode, and `as_of` is invalid outside `as_of` or
  `current_state`; and
- `timeline_id` scopes a temporal stream without exposing Cortex namespace internals.

All mapping decoders reject unknown contract fields. This strictness makes misspellings and schema
version mismatches visible at the connector boundary instead of silently replacing intent with
defaults.

## Provisional REST surface

The route names are planning-level and may change before the first contract release. The object
semantics and safety properties are the stable part.

```text
SOURCES
POST   /v1/sources
GET    /v1/sources/{source_id}
GET    /v1/sources/{source_id}/status

SYNCHRONIZATION
POST   /v1/sync-runs/{sync_id}/batches
POST   /v1/sync-runs/{sync_id}:commit
GET    /v1/sync-runs/{sync_id}

CONSUMPTION
POST   /v1/queries
POST   /v1/context-packs
GET    /v1/evidence/{evidence_id}
GET    /v1/queries/{query_id}/explanation

LEARNING
POST   /v1/outcomes

OBSERVATION
POST   /v1/subscriptions
DELETE /v1/subscriptions/{subscription_id}
```

The current `/v1/documents`, `/v1/retrievals`, and `/v1/feedback` endpoints remain supported while
the new contract is introduced. Implementation should reuse the same application services instead
of creating a parallel retrieval or learning path.

## Python SDK target

The SDK is a thin typed client and synchronization helper, not a second domain layer.

```python
from cortex import CortexClient, Record

client = CortexClient(base_url="http://127.0.0.1:8765")

with client.sync(source="my-app", scope="project:payments") as sync:
    sync.upsert(
        Record(
            external_id="ticket:184",
            external_version="7",
            content="Move refresh-token storage to PostgreSQL.",
            modality="text",
            occurred_at="2026-09-18T12:00:00Z",
        )
    )
    sync.commit(cursor="tickets-page:42")

result = client.retrieve(
    scope="project:payments",
    query="Why are refresh tokens stored in PostgreSQL?",
    budget_tokens=4_000,
)

client.record_outcome(
    retrieval_id=result.retrieval_id,
    used_evidence_ids=(result.items[0].evidence_id,),
    outcome="positive",
    reason="Used by the agent to explain the architecture decision.",
)
```

The SDK must expose structured errors, request IDs, timeouts, retry guidance, pagination/batching,
and an explicit close/context-manager lifecycle. It must not hide partial sync failure or retry
non-idempotent operations automatically.

## Agent-facing MCP adapter

MCP wraps the consumer and outcome portions of the contract:

| Tool | Classification | Behavior |
|---|---|---|
| `cortex_search` | read-only | Search permitted evidence and return compact source-aware results |
| `cortex_build_context` | read-only | Produce a bounded context pack with retrieval identity |
| `cortex_get_evidence` | read-only | Hydrate permitted evidence and provenance by evidence ID |
| `cortex_explain_retrieval` | read-only | Return score/path/lineage diagnostics appropriate to the caller |
| `cortex_record_outcome` | write | Attribute a positive or negative outcome to returned evidence |

Bulk sync, connector administration, tag resolution, retention, and erasure are not general agent
tools. They remain REST/SDK administration operations with narrower permissions.

Local IDE integration should use stdio or loopback under the local-user trust boundary. Remote MCP
uses Streamable HTTP only after the hosted security gate. Tool descriptions and structured results
must remain stable enough for agents to select them reliably, and read/write annotations must be
accurate.

## Scope and authorization model

The accepted future scope dimensions are organization, project, user, device/private overlay,
visibility, and contribution policy. Namespace may remain an indexing/routing value but cannot
grant access.

Remote request flow:

```text
verified credential
    -> principal
        -> allowed operations and scopes
            -> query/sync policy
                -> repository filters on every candidate and hydration path
```

Initial permissions should separate:

- `memory.read`;
- `outcome.write`;
- `source.sync`;
- `source.delete`;
- `review.write`;
- `subscription.manage`; and
- administrative scope management.

No remote implementation passes its gate until adversarial tests show that list, search,
relationship traversal, evidence hydration, diagnostics, and feedback cannot cross a forbidden
scope.

## Inspection and observability

Connector authors need to debug their mapping without inspecting Cortex tables. The supported
surface should eventually show:

- source and connector version;
- last committed cursor and sync time;
- accepted, idempotent, rejected, tombstoned, and pending record counts;
- per-record validation errors with safe external identity;
- enrichment state independent from canonical sync state;
- retrieval IDs, evidence provenance, and explanation summaries;
- outcome acceptance/rejection reason; and
- request/trace IDs usable across REST, SDK, MCP, jobs, and logs.

Payloads, private metadata, credentials, and unrestricted graph identifiers must not appear in
cross-scope metrics or ordinary operational logs.

## Delivery plan

| Phase | Status | Deliverable | Exit gate |
|---|---|---|---|
| C0. Freeze boundary | passed | ADR-0020 and this plan | Architecture, vocabulary, non-goals, and order are reviewed and committed |
| C1. Contract fixtures | passed | Transport-independent request/response models and fixtures | DevUI-like and Slack-like fixtures validate without core-specific input fields |
| C2. External record lifecycle | passed | Source registry, stable external identity, versions, relations, sync runs, tombstones | Memory/SQLite/PostgreSQL parity; replay/update/delete tests pass |
| C3. Python SDK | passed | Typed client, batch sync helper, retrieval, outcomes, contract-test kit | A connector uses only the SDK and its own mapping code |
| C4. Local MCP adapter | passed | Stdio read and outcome tools | REST and MCP return policy-equivalent results for fixed fixtures |
| C5. DevUI conformance probe | passed | Structured code/architecture fixture and contract validation | Generic records preserve source identity and explicit relations without core special cases |
| C6. Slack conformance probe | passed | Mutable conversation fixture and lifecycle validation | Same contract represents threads, edits, deletions, timestamps, cursors, and scope metadata |
| C7. Hosted remote integration | deferred | Authenticated HTTPS REST and Streamable HTTP MCP | D3b, scope/authorization, migration, deletion, backup, audit, rate-limit, and incident gates pass |

C1 passed under the current local deployment. C2 passed on 2026-09-19. The same lifecycle behavior
suite now runs against memory, SQLite, and PostgreSQL and covers exact replay, changed versions and
predecessor lineage, relations, tombstones, repairable and terminal partial failures, scope checks,
and cursor commits.
SQLite reopen and migration behavior and live PostgreSQL schema/runtime behavior are automated.
The branch-wide acceptance run passed 249 tests and 5 subtests with PostgreSQL enabled against an
isolated database; the canonical corpus was not opened or upgraded.

C2 stores canonical external-record lifecycle state. Projection of those records into Cortex's
retrieval atoms and transport-level access are not silently included in this milestone: they are
proved through the SDK and reference-connector round trips in C3, C5, and C6. C7 must not be
un-deferred merely to demonstrate Slack; a temporary tunnel is a demo shortcut, not the supported
security architecture.

C3 exposes source registration, consolidated sync batches, run inspection, explicit cursor
commit, scoped queries, context packs, and attributable outcomes through the loopback REST API and
the typed `cortex` Python package. The SDK has
structured errors, timeout configuration, explicit close/context-manager behavior, no automatic
retry, and a session helper that never commits on context exit. Its standalone contract-test kit
validates source/batch mappings and full query/context/outcome fixture linkage without importing a
repository. The generic projection derives deterministic internal routing from `Scope`, keeps
external record envelopes canonical, creates version lineage, suppresses tombstoned projections,
and exposes only opaque evidence IDs. Cursor commit is blocked until accepted records and
tombstones are projected. Outcome learning accepts only evidence returned by the named retrieval.

C4 implements a local stdio MCP server over that same access service. Its five tools cover scoped
search, bounded context construction, retrieval-bound evidence hydration, caller-safe retrieval
explanation, and attributable outcome reporting. Tool annotations distinguish read operations
from the non-destructive outcome write. Internal atom identity, bulk sync, connector
administration, and network transport are not exposed. In-process protocol tests compare MCP with
REST, and a subprocess test verifies that stdout remains protocol-clean. The adapter also passed
the query/outcome flow against live PostgreSQL in an isolated database.

## Reference connector conformance sequence

### DevUI

DevUI-like fixtures test stable structured identity, explicit relations, and mapping retrieval back
to source-owned identities. The operational mapper belongs in the DevUI repository.

```text
files/functions/modules/proposals
    -> DevUI mapping
        -> generic records and relations
            -> Cortex retrieval
                -> external IDs
                    -> highlight/open DevUI entity
```

### Slack

Slack-like fixtures test continuous ingestion, thread relationships, mutable records, deletion,
source timestamps, cursors, and scope metadata. OAuth, polling, rate limits, and operational
mapping belong in a separate Slack connector repository.

```text
messages/threads/edits/deletions
    -> Slack mapping
        -> the same records, relations, sync runs, and scopes
```

The two probes justified only one shared SDK abstraction: `sync_source_batches` validates one run,
submits caller-ordered batches, withholds cursor commit after incomplete acceptance, and never owns
source mapping or retries. The evidence does not justify a base class, generator, or
`cortex connector init` scaffolding command.

C5 and C6 are conformance milestones, not built-in integration claims. Source-specific runtime
packages and commands were removed from the Cortex distribution in accordance with ADR-0021.
The generic fixtures, lifecycle tests, SDK helper, and compatibility conclusions remain. See
[reference connector conformance](REFERENCE-CONNECTOR-CONFORMANCE.md).

## Acceptance matrix

The connector API is not ready until these behaviors are automated:

| Area | Required proof |
|---|---|
| Identity | Same version replay creates no duplicate; changed version remains traceable |
| Atomicity | Failed batch/sync does not advance its committed cursor |
| Resume | Crash after durable batch can replay and commit safely |
| Relations | Out-of-order endpoints resolve without invented identity |
| Deletion | Authorized tombstone leaves normal retrieval; unauthorized deletion fails closed |
| Scope | Forbidden records cannot appear through search, traversal, hydration, diagnostics, or feedback |
| Provenance | Every result maps to permitted source/external identity and canonical lineage |
| Learning | Outcome can credit only evidence returned by its retrieval |
| Degradation | Cortex remains usable when optional processors are unavailable |
| Transport parity | REST, SDK, and MCP enforce the same retrieval and outcome rules |
| Connector independence | DevUI and Slack require mapping code, not source-specific Cortex core branches |
| Operations | Request IDs, safe logs, status, retry guidance, backup, and migration behavior are documented |

## Failure model

- Invalid individual records are reported without accepting malformed evidence silently.
- Optional processor failure does not roll back canonical source capture.
- Source API rate limits pause connector progress without fabricating completion.
- A stale cursor fails explicitly or triggers connector-controlled reconciliation.
- Relationship targets that never arrive remain unresolved/quarantined rather than guessed.
- Webhook delivery is at least once; event IDs make consumer deduplication possible.
- Cortex unavailability must not corrupt the source application. DevUI, Slack, Git, and other
  sources remain authoritative for their own operation.
- Remote identity or policy uncertainty fails closed.

## Explicit non-goals for the first implementation

- a public connector marketplace;
- source-specific logic in the Cortex core;
- arbitrary agent access to ingestion, deletion, or tag review;
- automatic promotion of connector-supplied model interpretations to canonical truth;
- exactly-once distributed delivery claims;
- remote hosting before the accepted security and recovery prerequisites;
- realtime collaborative state synchronization through Cortex; and
- a large connector framework before external connector repositories expose real repetition.

## Decisions required during implementation

The following are intentionally left for their implementation gates rather than guessed here:

1. The exact persistent schema for source identity, external versions, sync runs, and tombstones.
2. The canonical encoding of organization/project/user/private scope and visibility.
3. Payload-reference immutability, availability, cache, and erasure rules for non-inline content.
4. Which source relations may activate immediately and which require proposal/review state.
5. The compatibility/versioning policy for REST schemas and the Python SDK.
6. Hosted identity provider and OAuth profile.
7. Webhook signing, retry schedule, retention, and dead-letter ownership.
8. How durable external record versions project into retrievable atoms, how superseded or
   tombstoned versions leave normal serving, and how scope selects an internal routing partition
   without becoming authorization.

Each decision must preserve ADR-0020 and receive repository, migration, authorization, and
contract tests proportional to its risk.

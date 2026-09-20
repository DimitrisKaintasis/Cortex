# Reference Connector Conformance

Status: C5/C6 validation complete; operational connectors are source-owned

Architecture decisions: [ADR-0020](decisions/0020-external-connector-and-agent-boundary.md) and
[ADR-0021](decisions/0021-source-owned-connector-packaging.md)

## Purpose

Cortex is designed to connect to arbitrary applications through one source-neutral contract. It
does not ship application-specific DevUI or Slack runtimes. The C5 and C6 exercises used two
unlike source shapes to test that claim:

- DevUI-like structured project data stressed stable entity identity, versions, relations,
  batching, and mapping evidence back to source-owned IDs.
- Slack-like mutable conversation data stressed incremental cursors, edits, threads, timestamps,
  tombstones, scope metadata, and attributable outcomes.

The exercises were validation probes, not a decision that Cortex should own those integrations.
Their operational mapping, credentials, polling, retry policy, and source UI behavior belong in
the DevUI repository or a separately maintained Slack connector repository.

## What remains in Cortex

| Surface | Responsibility |
|---|---|
| REST/OpenAPI | Canonical software-to-software connector protocol |
| `cortex` Python SDK | Typed transport over the public protocol |
| `Source`, `Record`, `Relation`, `Tombstone`, `SyncRun` | Source-neutral lifecycle vocabulary |
| `Query`, `ContextPack`, `Outcome` | Source-neutral retrieval and attribution vocabulary |
| `sync_source_batches` | Validate and explicitly commit one ordered run without hidden retries |
| contract-test kit | Validate connector-owned mappings without a Cortex repository |
| DevUI-like and Slack-like JSON fixtures | Preserve unlike conformance cases as regression inputs |
| lifecycle/projection tests | Prove replay, versions, relations, tombstones, scopes, retrieval, and outcomes |

Cortex contains no Slack OAuth client, Slack polling loop, DevUI exporter, product-specific CLI,
or source-specific mapper package.

## Connector repository shape

An external integration should depend on the public SDK and own its source boundary:

```text
source-owned repository
├── source API/export client and credentials
├── source-specific models
├── mapping to Cortex public contracts
├── cursor and retry policy
├── source UI navigation from returned external identity
└── contract fixtures tested with cortex.validate_source_sync

Cortex repository
├── generic REST API and MCP adapter
├── generic Python SDK
├── contract validation and sync helper
└── source-neutral conformance fixtures and tests
```

The connector process sends normalized records and source provenance to Cortex. It does not write
atoms, tags, weights, or database rows. Cortex does not receive reusable upstream credentials.

## Retained conformance cases

The checked-in fixtures remain intentionally source-shaped while using only generic contract
fields:

- `evals/connector_contract_devui_v1.json` covers structured records and explicit relations.
- `evals/connector_contract_slack_v1.json` covers two versions of one object, a reply relation, an
  incremental cursor, and a tombstone.

`validate_connector_fixture` and `validate_source_sync` reject unknown fields, source mismatches,
conflicting identity, duplicate or non-contiguous batches, acknowledgement mismatches, and outcomes
that claim evidence outside their context pack. Repository and transport suites separately prove
the generic lifecycle behavior on memory, SQLite, PostgreSQL, REST, SDK, and MCP surfaces.

The public `sync_source_batches` helper is the only abstraction extracted from the two exercises.
It registers a source, submits caller-ordered batches, raises `ConnectorSyncRejected` after an
incomplete acknowledgement, and withholds the cursor commit unless every batch succeeds. It does
not own mapping, source API access, automatic retry, or cursor acquisition.

## Verification

Run the connector boundary tests with:

```powershell
python -m pytest tests/test_connector_contracts.py tests/test_connector_lifecycle.py `
  tests/test_connector_projection.py tests/test_cortex_sdk.py -q
```

Before publishing an external connector, its own repository should also test:

- stable external IDs and changed versions;
- exact replay and crash resume;
- relation endpoint behavior;
- edits and authorized tombstones where applicable;
- explicit scope/visibility mapping;
- cursor advancement only after durable acknowledgement;
- evidence-to-source navigation; and
- outcomes limited to evidence returned by the named retrieval.

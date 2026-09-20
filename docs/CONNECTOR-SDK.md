# Connector SDK quickstart

Status: C3 sync, retrieval, and attributable outcome methods implemented for the local API.

## Mental model

Your connector owns source-specific API calls, credentials, rate limits, and mapping. Cortex owns
contract validation, durable replay identity, version lineage, tombstones, relations, and cursor
commit. The connector process talks only to the loopback REST API through the `cortex` package; it
does not import a repository or create atoms, tags, weights, or database rows.

```text
source API -> your mapping -> Source / Record / Relation / Tombstone
                              -> CortexClient -> loopback REST -> durable projection
application question -> Query -> ContextPack with external RecordRef + opaque evidence IDs
application result -> Outcome using returned evidence IDs -> bounded Cortex learning
```

## Install and run locally

Install the API server and SDK in a development checkout:

```powershell
python -m pip install -e ".[api,sdk]"
python -m data_retrieval serve-api --db .\cortex.sqlite3 --port 8765
```

The server runs on `127.0.0.1`; it has no remote authentication boundary. Do not expose it through
a public bind or tunnel. Open `http://127.0.0.1:8765/docs` to inspect the generated Source and
SyncBatch schemas.

## Validate mapping fixtures first

The contract kit needs no server or database:

```python
import json
from pathlib import Path

from cortex import validate_connector_fixture

fixture = json.loads(Path("my_connector_fixture.json").read_text(encoding="utf-8"))
report = validate_connector_fixture(fixture)
print(report)
```

It rejects unknown fixture sections, invalid public objects, source mismatches, conflicting run
identity, duplicate batch identity, non-contiguous sequence numbers, acknowledgement mismatches,
and outcomes that claim evidence outside their context pack.

## Submit and commit a sync

Create a `Source`, register it once, and build `Record` objects from your application data. A
record version identity must be stable: replaying it with different content is a terminal conflict.

```python
from datetime import UTC, datetime

from cortex import (
    ConnectorCapability,
    ContributionPolicy,
    CortexClient,
    Record,
    RecordModality,
    RecordPayload,
    RecordRef,
    Scope,
    Source,
    SourceRef,
    SyncMode,
    SyncRun,
    Visibility,
)

source_ref = SourceRef("my-app", "production")
scope = Scope(
    visibility=Visibility.PROJECT,
    organization_id="org:acme",
    project_id="project:payments",
    contribution_policy=ContributionPolicy.PRIVATE,
)
source = Source(
    source=source_ref,
    connector_id="my-app-connector",
    connector_version="0.1.0",
    owner_scope=scope,
    capabilities=(ConnectorCapability.SOURCE_SYNC,),
)
run = SyncRun(
    request_id="tickets-page-42",
    source=source_ref,
    mode=SyncMode.FULL,
    started_at=datetime.now(UTC),
    proposed_cursor="tickets-page:42",
)
record = Record(
    ref=RecordRef(source_ref, "ticket:184", "version:7"),
    modality=RecordModality.TEXT,
    payload=RecordPayload(inline="Move refresh-token storage to PostgreSQL."),
    scope=scope,
    observed_at=datetime.now(UTC),
)

with CortexClient(base_url="http://127.0.0.1:8765", timeout=30.0) as client:
    client.register_source(source)
    with client.sync(run) as sync:
        acknowledgement = sync.submit(
            batch_id="tickets-page-42:batch:0",
            records=(record,),
        )
        if not acknowledgement.complete:
            for failure in acknowledgement.failures:
                print(failure.item_id, failure.code, failure.retryable)
            raise RuntimeError("repair or abandon this run; cursor was not advanced")
        committed = sync.commit(request_id="tickets-page-42:commit")

print(committed.committed_cursor)
```

Leaving `client.sync(...)` never commits automatically. This is deliberate: an exception after a
durable batch must not make the connector claim its upstream cursor advanced. Reopen a session
with `start_sequence=` to resume, replay an identical batch safely, repair retryable item failures
in a later batch, and call `commit` only after every required item is durable.

`CortexApiError` exposes `status_code`, `retryable`, and `retry_after`. These are guidance for the
connector's explicit retry policy; the SDK does not automatically retry writes.

After the DevUI-like and Slack-like conformance exercises proved the same orchestration, the SDK added the
narrow `sync_source_batches` helper. It validates that batches form one contiguous run, registers
the source, submits in caller-defined order, raises `ConnectorSyncRejected` on any incomplete
acknowledgement, and commits only after all batches succeed. Mapping, batching, retry, source API,
and cursor acquisition remain connector-owned.

## Retrieve context and report an outcome

Connectors never submit namespaces or atom IDs. Cortex derives an internal routing namespace from
the public `Scope`, returns the source-owned `RecordRef`, and uses opaque evidence IDs for outcome
attribution:

```python
from cortex import Outcome, OutcomeValue, Query

with CortexClient(base_url="http://127.0.0.1:8765") as client:
    context = client.query(
        Query(
            request_id="answer-ticket:184",
            query="Where should refresh tokens be stored?",
            scope=scope,
            top_k=5,
            budget_tokens=1200,
        )
    )
    if context.items:
        accepted = client.report_outcome(
            Outcome(
                request_id="answer-ticket:184:outcome",
                retrieval_id=context.retrieval_id,
                used_evidence_ids=(context.items[0].evidence_id,),
                outcome=OutcomeValue.POSITIVE,
                occurred_at=datetime.now(UTC),
                reason="The evidence was used in the final answer.",
            )
        )
```

Query and outcome request IDs are replay-safe. Reusing either ID with a changed payload is rejected.
An outcome can credit only evidence returned by its named retrieval. Updates create `SUPERSEDES`
lineage; tombstones remove matching projected evidence from normal serving without deleting its
history. Referenced payload fetching is not enabled yet, so sync rejects `reference_uri` records
before the run becomes durable; submit inline content for this local milestone.

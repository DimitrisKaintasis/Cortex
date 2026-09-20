# DevUI Reference Connector

Status: C5 passed

Architecture decision: [ADR-0020](decisions/0020-external-connector-and-agent-boundary.md)

## Purpose and boundary

`cortex_devui` is the first source-specific reference connector. It demonstrates that an
application can integrate with Cortex using only the public `cortex` SDK and contract types. The
package does not import repositories, atoms, tags, weights, migrations, or any module under
`data_retrieval`.

DevUI remains authoritative for its project graph and for opening or highlighting an entity.
Cortex stores replay-safe projections for retrieval and returns the original DevUI external
identity. This connector does not read a DevUI database directly; its input boundary is a
source-native snapshot exported by DevUI or an adapter in the DevUI process.

```text
DevUI snapshot
    -> cortex_devui mapping
        -> public Source / Record / Relation batches
            -> CortexClient -> loopback REST API
                -> ContextPack
                    -> DevUIEntityPointer -> open file/entity in DevUI
```

## Source-native snapshot

A snapshot has a stable `snapshot_id`, a proposed source `cursor`, one capture timestamp, entities,
and relations. The checked-in [reference fixture](../evals/devui_snapshot_v1.json) includes every
accepted C5 entity type:

| DevUI entity | Cortex modality | Stable external identity example |
|---|---|---|
| file | code | `file:src/auth/service.py` |
| function | code | `function:src/auth/service.py:login` |
| module | text | `module:authentication` |
| proposal | text | `proposal:session-boundary` |

Each entity supplies its own version. Reusing an `(external_id, external_version)` pair with
changed content is a terminal contract conflict; DevUI must issue a new version when the entity
changes. Relations name exact endpoint versions and are rejected by the mapper if either endpoint
is absent from the snapshot.

Navigation metadata is deliberately small: `entity_type`, `display_name`, `file`, and `line`.
Connector-specific metadata may be added when it is JSON-safe, but it must not replace stable
identity.

## Install and run

Install the local API and DevUI connector extras:

```powershell
python -m pip install -e ".[api,devui]"
python -m data_retrieval serve-api --db C:\absolute\path\to\cortex.sqlite3
```

In another terminal, submit a snapshot:

```powershell
cortex-devui .\devui-snapshot.json `
  --organization-id org:portfolio `
  --project-id project:cortex `
  --source-instance project:cortex
```

The command defaults to `http://127.0.0.1:8765`. It prints the committed cursor as JSON only after
all record and relation batches are accepted. An incomplete batch raises an error and never sends
the commit request, so Cortex cannot falsely claim that DevUI's source cursor advanced.

`batch_size` defaults to 500. The mapper sends all record chunks before relation chunks because a
relation is valid only after both exact endpoint versions are durable. Batch IDs, sequence
numbers, run IDs, and commit IDs are deterministic, making an identical snapshot replay-safe.

## Library use and navigation round trip

```python
import json
from pathlib import Path

from cortex import CortexClient, Query
from cortex_devui import (
    DevUIConnector,
    DevUIConnectorConfig,
    DevUIMapper,
    snapshot_from_mapping,
)

snapshot = snapshot_from_mapping(
    json.loads(Path("devui-snapshot.json").read_text(encoding="utf-8"))
)
config = DevUIConnectorConfig(
    organization_id="org:portfolio",
    project_id="project:cortex",
    source_instance="project:cortex",
)
mapper = DevUIMapper(config)

with CortexClient(base_url="http://127.0.0.1:8765") as client:
    DevUIConnector(client, mapper).sync_snapshot(snapshot)
    context = client.query(
        Query(
            request_id="devui:find-login",
            query="login",
            scope=config.scope,
        )
    )
    pointers = mapper.pointers(context)

for pointer in pointers:
    print(pointer.entity_type, pointer.file, pointer.line, pointer.external_id)
```

`DevUIEntityPointer` validates that evidence belongs to the configured DevUI source and that its
external identity agrees with its metadata. The calling DevUI integration can then open
`pointer.file` at `pointer.line`, or route modules and proposals using `external_id` and `locator`.

## Acceptance evidence

Automated tests prove:

- all four entity types map without Cortex-internal fields;
- six deterministic batches preserve contiguous order and keep relations after records;
- missing or wrong relation endpoint versions fail before network I/O;
- exact snapshot replay returns the same committed cursor;
- a rejected batch never sends a cursor commit;
- a real SDK-to-REST-to-SQLite flow returns a function that maps back to its DevUI file and line;
- the same SDK and navigation round trip passes against live PostgreSQL;
- the returned opaque evidence ID can report an attributable outcome; and
- the reference package contains no `data_retrieval` import.

Run the focused suite with:

```powershell
python -m pytest tests/test_devui_connector.py tests/test_cortex_sdk.py `
  tests/test_connector_projection.py -q
```

## Integration friction log

| Friction | Resolution for C5 | Follow-up |
|---|---|---|
| Relations cannot precede missing endpoint versions | Emit all record chunks before relation chunks | Reassess only if Slack requires durable unresolved relations |
| Large snapshots need bounded requests | Deterministic configurable chunking, default 500 items | Measure real DevUI payload sizes before changing the default |
| Retrieved identities must reopen source entities | Preserve typed external IDs plus file/line navigation metadata | DevUI owns the final UI routing action |
| Referenced payload fetching is not accepted yet | Submit inline searchable content | Revisit immutable payload references at the hosted security gate |
| The DevUI application repository is not present in this workspace | Define and test a strict source-native JSON handoff | Wire the real DevUI exporter without changing Cortex core contracts |
| DevUI and Slack repeated safe submit/commit orchestration | Extracted only the public `sync_source_batches` helper | Keep source models and mapping separate |

The final rows are intentional boundaries, not hidden completion claims. C5 proves the
connector and round trip. Deployment into the separate DevUI application still requires that
application to emit the documented snapshot shape.

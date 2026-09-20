# Slack Reference Connector

Status: C6 passed

Architecture decision: [ADR-0020](decisions/0020-external-connector-and-agent-boundary.md)

## Purpose and boundary

`cortex_slack` proves that the same Cortex connector contract handles a mutable conversational
source. It maps source-native Slack event pages through only the public `cortex` SDK. The package
does not import Cortex repositories, atoms, tags, migrations, or other implementation modules.

Slack remains authoritative for messages, membership, retention, and deletion. The Slack-facing
process owns OAuth tokens, API calls, event subscriptions, pagination, rate-limit backoff, and
event export. Cortex never receives Slack credentials. This reference connector begins at the
decoded event-page boundary.

```text
Slack API/events -> source-owned polling and retry -> Slack event pages
    -> explicit channel/project allowlist
        -> cortex_slack mapping
            -> public Record / Relation / Tombstone batches
                -> CortexClient -> loopback REST API
                    -> ContextPack -> SlackMessagePointer
```

## Identity, changes, and access mapping

| Slack concept | Cortex representation |
|---|---|
| workspace | source instance `workspace:<workspace_id>` |
| message object | stable ID `channel:<channel_id>:message:<message_ts>` |
| original message | version `edited:0` |
| edited message | same stable ID, version `edited:<edited_ts>` |
| thread reply | `reply_to` relation to an exact root-message version |
| deletion event | tombstone `deleted:<event_ts>` targeting the message object |
| event page cursor | incremental run `previous_cursor` and `proposed_cursor` |
| channel access | required channel-to-project allowlist producing project scope |

An unknown channel fails before mapping or network I/O. This is deliberate fail-closed routing:
message metadata cannot assign its own Cortex project. Remote authorization is still deferred to
C7; the allowlist is a local connector policy, not a replacement for authenticated authorization.

Edits are not destructive overwrites. Cortex retains version lineage while current-state queries
serve only the latest version. Tombstones suppress a deleted message from normal current serving
without erasing its audit history.

## Source-native event pages

The checked-in [reference export](../evals/slack_event_pages_v1.json) contains two incremental
pages. The first creates the initial cursor and messages. The second uses that exact cursor and
contains an edit, a reply, and a deletion. The decoder requires every adjacent page to form a
contiguous cursor chain.

Replies must name the exact `thread_root_version`. Cortex does not guess which historical root a
reply referenced. Event delivery may be at least once: deterministic page, batch, relation,
tombstone, run, and commit identities make exact replay safe.

## Install and run

Install the local API and Slack connector extras:

```powershell
python -m pip install -e ".[api,slack]"
python -m data_retrieval serve-api --db C:\absolute\path\to\cortex.sqlite3
```

In another terminal, submit exported pages:

```powershell
cortex-slack .\slack-event-pages.json `
  --organization-id org:acme `
  --workspace-id T001 `
  --channel-project C-AUTH=project:payments
```

Repeat `--channel-project` for each approved channel. The command defaults to the loopback API at
`http://127.0.0.1:8765` and a 500-item batch size. It prints committed cursor acknowledgements as
JSON only after all pages finish.

For each page the mapper sends record chunks first, reply relations second, and tombstones last.
The shared public `sync_source_batches` helper validates one run, stops on a partial batch
acknowledgement, and sends the cursor commit only after every batch is durable. A source API error,
rate limit, Cortex outage, malformed relation, or rejected item therefore cannot fabricate cursor
progress.

## Retrieval and Slack navigation

`SlackMapper.pointers(context)` selects evidence owned by the configured Slack workspace and
returns `SlackMessagePointer` values with workspace, channel, message timestamp, thread timestamp,
author, project, edit timestamp, and opaque evidence identity. It validates the external ID,
metadata, and channel-derived project scope before returning a pointer.

The Slack-facing application can turn `(workspace_id, channel_id, message_ts)` into its own
permalink or UI action. Cortex deliberately does not invent a public URL or assume a Slack domain.
If an agent uses the evidence, it reports the pointer's `evidence_id` with the context's
`retrieval_id` through the normal outcome API.

## Verification

Run the focused suite with:

```powershell
python -m pytest tests/test_slack_connector.py tests/test_devui_connector.py `
  tests/test_cortex_sdk.py tests/test_connector_projection.py -q
```

Automated acceptance proves:

- channel/project routing fails closed for unknown channels;
- cursor pages must be contiguous and exact replay is idempotent;
- edits create a new version of the same message object;
- replies name an exact root version and map to `reply_to` relations;
- deletion tombstones leave current-state retrieval;
- message evidence maps back to validated Slack navigation identity;
- outcomes can credit the retrieved current message;
- the same SDK-to-REST flow passes on SQLite and live PostgreSQL; and
- the package imports no Cortex implementation module.

## Integration friction and extracted reuse

| Friction observed across DevUI and Slack | Resolution |
|---|---|
| Both connectors repeated safe register/validate/submit/commit orchestration | Extracted the narrow public `sync_source_batches` SDK helper |
| Source models and ordering differ substantially | Kept DevUI snapshot and Slack event-page mappers separate |
| Slack relations may target prior-page records | Require exact root versions; let Cortex validate durable endpoint existence |
| One page may update and delete related objects | Separate records, relations, and tombstones into ordered batches |
| Slack channel metadata is not authorization | Require an external channel/project allowlist and keep hosted auth deferred |
| Slack API/OAuth implementation is absent from this workspace | Document a strict event-page handoff; wire polling in the source application |

The evidence does not justify a broad base connector, schema generator, or plugin marketplace yet.
The shared orchestration helper is the only repeated behavior extracted after both reference
connectors; their source semantics remain explicit and reviewable.

# Local MCP Runbook

Status: local stdio adapter

Architecture decision: [ADR-0020](decisions/0020-external-connector-and-agent-boundary.md)

## Purpose and boundary

The MCP adapter lets an IDE or another local agent host use Cortex without understanding its
repository, atom, graph, or ranking internals. It wraps the same `ConnectorAccessService` used by
the REST and Python SDK surfaces, so transport choice does not create a second retrieval policy.

This is a local-user-trust interface. It communicates over stdio with the process that launched
it. It has no remote listener, accounts, authentication, tenant authorization, or source
administration tools. Do not expose it through a network bridge. Authenticated Streamable HTTP
MCP remains part of the deferred C7 hosted-security phase.

The complete connector flow still has four distinct processes:

1. A connector maps and syncs source-owned records through REST or the Python SDK.
2. An agent searches or builds a bounded context pack through MCP.
3. The agent hydrates or explains only evidence returned by a named retrieval.
4. The agent records an attributable outcome against evidence it actually used.

MCP intentionally covers processes 2-4. Bulk sync, connector registration, cursor commits, tag
review, retention, and deletion remain explicit administration operations outside general agent
tools.

## Install and start

Install the MCP transport in the Python environment from which the agent host will launch Cortex:

```powershell
python -m pip install -e ".[mcp]"
```

Add `postgres` when Cortex uses PostgreSQL:

```powershell
python -m pip install -e ".[mcp,postgres]"
```

Run the adapter directly to inspect startup errors. Protocol messages use stdout, so Cortex sends
its startup status and application logs to stderr.

```powershell
python -m data_retrieval serve-mcp --db C:\absolute\path\to\cortex.sqlite3
```

The process runs until the host closes it or `Ctrl+C` stops it. SQLite remains canonical and
durable; the MCP process contains no separate application state.

## Configure an MCP host

Host configuration formats and locations vary, but a typical stdio entry has this shape:

```json
{
  "mcpServers": {
    "cortex-local": {
      "command": "C:\\absolute\\path\\to\\python.exe",
      "args": [
        "-m",
        "data_retrieval",
        "serve-mcp",
        "--db",
        "C:\\absolute\\path\\to\\cortex.sqlite3"
      ]
    }
  }
}
```

Use the Python executable where this project and its `mcp` extra are installed. Use an absolute
database path because an MCP host may launch child processes from a different working directory.

For PostgreSQL, omit `--db` and inject `DATA_RETRIEVAL_POSTGRES_DSN` using the host's protected
environment configuration. Do not commit the DSN to a host config or place it in command-line
arguments, where process inspection and shell history may expose it.

## Agent tool contract

| Tool | Safety annotation | Result and constraint |
|---|---|---|
| `cortex_search` | read-only, idempotent | Compact scoped `ContextPack` with a retrieval ID and opaque evidence IDs |
| `cortex_build_context` | read-only, idempotent | Token-bounded context with temporal controls |
| `cortex_get_evidence` | read-only, idempotent | One evidence item only when both retrieval and evidence IDs match |
| `cortex_explain_retrieval` | read-only, idempotent | Caller-safe score, source, and lineage evidence; never internal atom IDs |
| `cortex_record_outcome` | non-destructive write, idempotent | Positive or negative outcome limited to evidence returned by that retrieval |

`request_id` is a caller-generated stable identity. Repeating it with the same input replays the
same query or outcome safely; reusing it with different input is rejected. Keep the returned
`retrieval_id` and `evidence_id` values together. Evidence IDs are opaque and are not general
lookup keys.

An empty or low-confidence context pack is a valid answer. The agent should respect its
`abstention_reason` rather than treating missing evidence as permission to broaden scope.

## Operations and troubleshooting

- If the host shows no Cortex tools, run the exact configured command in a terminal and inspect
  stderr for import, path, migration, or database errors.
- If the Python module is missing, point `command` at the interpreter used for the editable
  install; activating a shell environment does not guarantee that a GUI host inherits it.
- If Cortex opens an unexpected empty SQLite database, replace the relative `--db` value with an
  absolute path.
- If PostgreSQL startup fails, verify that the host passes `DATA_RETRIEVAL_POSTGRES_DSN` to the
  child process and that `.[postgres]` is installed.
- Never add debug `print` calls to the stdio server. stdout belongs exclusively to the MCP
  protocol; use stderr or structured logging instead.

## Verification

Run the adapter tests after changing the MCP schema, annotations, storage configuration, or access
service:

```powershell
python -m pytest tests/test_mcp_server.py tests/test_api.py tests/test_connector_projection.py -q
```

The suite compares MCP and REST output for the same fixture, checks evidence membership and hidden
internal IDs, replays an MCP outcome through REST, and launches a real stdio child process. When
`DATA_RETRIEVAL_TEST_POSTGRES_DSN` is set to an isolated test database, it also runs the MCP query
and outcome flow through PostgreSQL.

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, cast

from fastapi.testclient import TestClient
from mcp import Client, StdioServerParameters

from data_retrieval.api import LocalApiConfig, create_app
from data_retrieval.connectors.codec import source_from_mapping, sync_batch_from_mapping
from data_retrieval.mcp_server import LocalMcpConfig, create_mcp_server
from data_retrieval.services.connector_sync import ConnectorSyncService
from data_retrieval.storage.postgresql import PostgreSQLRepository
from data_retrieval.storage.sqlite import SQLiteRepository


def _fixture() -> dict[str, object]:
    path = Path(__file__).parents[1] / "evals" / "connector_contract_devui_v1.json"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


class LocalMcpServerTests(unittest.TestCase):
    def test_mcp_tools_match_rest_policy_and_hide_internal_identity(self) -> None:
        fixture = _fixture()
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "cortex.sqlite3"
            with SQLiteRepository(database) as repository:
                sync = ConnectorSyncService(repository)
                sync.register_source(source_from_mapping(fixture["registration"]))
                sync.submit_batch(sync_batch_from_mapping(fixture["sync_batch"]))

            rest_query = dict(cast(dict[str, Any], fixture["query"]))
            rest_query["request_id"] = "query:rest-parity"
            with TestClient(
                create_app(LocalApiConfig(database_path=database))
            ) as rest_client:
                rest_context = rest_client.post("/v1/queries", json=rest_query)
            self.assertEqual(rest_context.status_code, 200)

            async def exercise() -> None:
                server = create_mcp_server(LocalMcpConfig(database_path=database))
                async with Client(server) as client:
                    listed = await client.list_tools()
                    tools = {tool.name: tool for tool in listed.tools}
                    self.assertEqual(
                        set(tools),
                        {
                            "cortex_search",
                            "cortex_build_context",
                            "cortex_get_evidence",
                            "cortex_explain_retrieval",
                            "cortex_record_outcome",
                        },
                    )
                    for name in tools:
                        annotations = tools[name].annotations
                        self.assertIsNotNone(annotations)
                        assert annotations is not None
                        self.assertEqual(
                            annotations.read_only_hint,
                            name != "cortex_record_outcome",
                        )
                        self.assertTrue(annotations.idempotent_hint)
                        self.assertFalse(annotations.destructive_hint)

                    query = cast(dict[str, Any], fixture["query"])
                    built = await client.call_tool(
                        "cortex_build_context",
                        {
                            "request_id": "query:mcp-parity",
                            "query": query["query"],
                            "scope": query["scope"],
                            "top_k": query["top_k"],
                            "budget_tokens": query["budget_tokens"],
                            "temporal_mode": query["temporal_mode"],
                            "reference_time": query["reference_time"],
                        },
                    )
                    self.assertFalse(built.is_error)
                    context = cast(dict[str, Any], built.structured_content)
                    self.assertEqual(context["items"], rest_context.json()["items"])
                    self.assertEqual(
                        context["low_confidence"], rest_context.json()["low_confidence"]
                    )
                    first = context["items"][0]
                    self.assertNotIn("atom_id", first)
                    self.assertIn("external_id", first["record"])

                    evidence = await client.call_tool(
                        "cortex_get_evidence",
                        {
                            "retrieval_id": context["retrieval_id"],
                            "evidence_id": first["evidence_id"],
                        },
                    )
                    self.assertEqual(evidence.structured_content, first)
                    rejected = await client.call_tool(
                        "cortex_get_evidence",
                        {
                            "retrieval_id": context["retrieval_id"],
                            "evidence_id": "evidence:not-returned",
                        },
                    )
                    self.assertTrue(rejected.is_error)

                    explained = await client.call_tool(
                        "cortex_explain_retrieval",
                        {"retrieval_id": context["retrieval_id"]},
                    )
                    explanation = cast(dict[str, Any], explained.structured_content)
                    self.assertEqual(
                        explanation["items"][0]["evidence_id"], first["evidence_id"]
                    )
                    self.assertNotIn("atom_id", json.dumps(explanation))

                    outcome_payload = {
                        "request_id": "outcome:mcp-parity",
                        "retrieval_id": context["retrieval_id"],
                        "used_evidence_ids": [first["evidence_id"]],
                        "outcome": "positive",
                        "occurred_at": "2026-09-18T12:31:00+03:00",
                        "reason": "Used by the MCP parity test.",
                    }
                    outcome = await client.call_tool(
                        "cortex_record_outcome", outcome_payload
                    )
                    self.assertFalse(outcome.is_error)
                    with TestClient(
                        create_app(LocalApiConfig(database_path=database))
                    ) as rest_client:
                        rest_outcome = rest_client.post(
                            "/v1/outcomes", json=outcome_payload
                        )
                    self.assertEqual(rest_outcome.status_code, 200)
                    self.assertEqual(outcome.structured_content, rest_outcome.json())

            asyncio.run(exercise())

    def test_cli_stdio_transport_has_clean_protocol_output(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "stdio.sqlite3"

            async def exercise() -> None:
                parameters = StdioServerParameters(
                    command=sys.executable,
                    args=[
                        "-m",
                        "data_retrieval",
                        "serve-mcp",
                        "--db",
                        str(database),
                    ],
                )
                async with Client(parameters) as client:
                    listed = await client.list_tools()
                    self.assertEqual(len(listed.tools), 5)
                    result = await client.call_tool(
                        "cortex_search",
                        {
                            "request_id": "query:stdio-empty",
                            "query": "nothing indexed",
                            "scope": {
                                "visibility": "project",
                                "organization_id": "org:test",
                                "project_id": "project:test",
                            },
                        },
                    )
                    self.assertFalse(result.is_error)
                    context = cast(dict[str, Any], result.structured_content)
                    self.assertEqual(context["items"], [])
                    self.assertEqual(
                        context["abstention_reason"], "no_relevant_evidence"
                    )

            asyncio.run(exercise())


@unittest.skipUnless(
    os.getenv("DATA_RETRIEVAL_TEST_POSTGRES_DSN"),
    "DATA_RETRIEVAL_TEST_POSTGRES_DSN is not configured",
)
class PostgreSQLMcpServerTests(unittest.TestCase):
    def test_query_and_outcome_round_trip_through_mcp(self) -> None:
        fixture = _fixture()
        dsn = os.environ["DATA_RETRIEVAL_TEST_POSTGRES_DSN"]
        with PostgreSQLRepository(dsn) as repository:
            sync = ConnectorSyncService(repository)
            sync.register_source(source_from_mapping(fixture["registration"]))
            sync.submit_batch(sync_batch_from_mapping(fixture["sync_batch"]))

        async def exercise() -> None:
            server = create_mcp_server(LocalMcpConfig(postgres_dsn=dsn))
            async with Client(server) as client:
                query = cast(dict[str, Any], fixture["query"])
                built = await client.call_tool(
                    "cortex_build_context",
                    {
                        "request_id": "query:mcp-postgres",
                        # Keep this assertion about transport/storage parity. PostgreSQL's
                        # web-search parser is intentionally stricter than SQLite's lexical
                        # channel for the fixture's full natural-language question.
                        "query": "login",
                        "scope": query["scope"],
                        "top_k": query["top_k"],
                        "budget_tokens": query["budget_tokens"],
                        "temporal_mode": query["temporal_mode"],
                        "reference_time": query["reference_time"],
                    },
                )
                self.assertFalse(built.is_error)
                context = cast(dict[str, Any], built.structured_content)
                self.assertTrue(context["items"])
                first = context["items"][0]
                self.assertNotIn("atom_id", first)

                outcome = await client.call_tool(
                    "cortex_record_outcome",
                    {
                        "request_id": "outcome:mcp-postgres",
                        "retrieval_id": context["retrieval_id"],
                        "used_evidence_ids": [first["evidence_id"]],
                        "outcome": "positive",
                        "occurred_at": "2026-09-20T12:31:00+03:00",
                        "reason": "Live PostgreSQL MCP transport parity.",
                    },
                )
                self.assertFalse(outcome.is_error)
                self.assertEqual(
                    outcome.structured_content["retrieval_id"], context["retrieval_id"]
                )

        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()

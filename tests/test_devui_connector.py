from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx
from fastapi.testclient import TestClient

from cortex import CortexClient, Outcome, OutcomeValue, Query, TemporalQueryMode
from cortex.testing import validate_source_sync
from cortex_devui import (
    DevUIConnector,
    DevUIConnectorConfig,
    DevUIEntityType,
    DevUIMapper,
    DevUIRelation,
    DevUISyncRejected,
    snapshot_from_mapping,
)
from cortex_devui.cli import build_parser
from data_retrieval.api import LocalApiConfig, create_app


class _ApiTransport(httpx.BaseTransport):
    def __init__(self, client: TestClient) -> None:
        self.client = client

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        response = self.client.request(
            request.method,
            request.url.raw_path.decode("ascii"),
            headers=dict(request.headers),
            content=request.content,
        )
        return httpx.Response(
            status_code=response.status_code,
            headers=response.headers,
            content=response.content,
            request=request,
        )


class DevUIConnectorTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture_path = Path(__file__).parents[1] / "evals" / "devui_snapshot_v1.json"
        self.snapshot = snapshot_from_mapping(
            json.loads(fixture_path.read_text(encoding="utf-8"))
        )
        self.config = DevUIConnectorConfig(
            organization_id="org:portfolio",
            project_id="project:cortex",
            source_instance="project:cortex",
            batch_size=2,
        )
        self.mapper = DevUIMapper(self.config)

    def test_source_native_snapshot_maps_to_chunked_public_contract(self) -> None:
        batches = self.mapper.batches(self.snapshot)
        report = validate_source_sync(self.mapper.source(), batches)

        self.assertEqual(report.record_count, 6)
        self.assertEqual(report.relation_count, 5)
        self.assertEqual(report.batch_count, 6)
        self.assertEqual([batch.sequence for batch in batches], list(range(6)))
        self.assertTrue(all(batch.records for batch in batches[:3]))
        self.assertTrue(all(batch.relations for batch in batches[3:]))
        modalities = {
            record.ref.external_id: record.modality.value
            for batch in batches
            for record in batch.records
        }
        self.assertEqual(modalities["file:src/auth/service.py"], "code")
        self.assertEqual(modalities["proposal:session-boundary"], "text")

    def test_relation_must_reference_the_snapshot_version(self) -> None:
        invalid = DevUIRelation(
            relation_id="calls:missing",
            relation_version="graph:43",
            relation_type="calls",
            source_external_id="function:src/auth/service.py:login",
            source_external_version="sha256:missing",
            target_external_id="function:src/auth/security.py:verify_password",
            target_external_version="sha256:verify-v2",
            observed_at=datetime(2026, 9, 20, 10, 0, tzinfo=UTC),
        )

        with self.assertRaisesRegex(ValueError, "source is missing"):
            self.mapper.batches(
                replace(self.snapshot, relations=(invalid,))
            )

    def test_sdk_sync_query_and_navigation_round_trip(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "devui.sqlite3"
            app = create_app(LocalApiConfig(database_path=database))
            with TestClient(app) as api_client:
                with CortexClient(
                    base_url="http://cortex.test",
                    transport=_ApiTransport(api_client),
                ) as client:
                    connector = DevUIConnector(client, self.mapper)
                    committed = connector.sync_snapshot(self.snapshot)
                    replayed = connector.sync_snapshot(self.snapshot)
                    self.assertEqual(committed, replayed)
                    self.assertEqual(committed.committed_cursor, "scan:43")

                    context = client.query(
                        Query(
                            request_id="devui-query:round-trip",
                            query="login",
                            scope=self.config.scope,
                            top_k=10,
                            budget_tokens=1200,
                            temporal_mode=TemporalQueryMode.CURRENT_STATE,
                            reference_time=datetime(
                                2026, 9, 20, 10, 30, tzinfo=UTC
                            ),
                        )
                    )
                    pointers = self.mapper.pointers(context)
                    login = next(
                        pointer
                        for pointer in pointers
                        if pointer.external_id
                        == "function:src/auth/service.py:login"
                    )
                    self.assertEqual(login.entity_type, DevUIEntityType.FUNCTION)
                    self.assertEqual(login.locator, "src/auth/service.py:login")
                    self.assertEqual(login.file, "src/auth/service.py")
                    self.assertEqual(login.line, 41)

                    accepted = client.report_outcome(
                        Outcome(
                            request_id="devui-outcome:round-trip",
                            retrieval_id=context.retrieval_id,
                            used_evidence_ids=(login.evidence_id,),
                            outcome=OutcomeValue.POSITIVE,
                            occurred_at=datetime(
                                2026, 9, 20, 10, 31, tzinfo=UTC
                            ),
                            reason="DevUI opened the returned function.",
                        )
                    )
                    self.assertEqual(accepted.retrieval_id, context.retrieval_id)

    def test_rejected_batch_never_commits_the_cursor(self) -> None:
        paths: list[str] = []

        def reject_first_batch(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path == "/v1/sources":
                return httpx.Response(201, json=json.loads(request.content))
            if request.url.path.endswith("/batches"):
                batch = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={
                        "run_request_id": batch["run"]["request_id"],
                        "batch_id": batch["batch_id"],
                        "sequence": batch["sequence"],
                        "acknowledged_at": "2026-09-20T10:01:00+00:00",
                        "accepted_records": 0,
                        "accepted_relations": 0,
                        "accepted_tombstones": 0,
                        "failures": [
                            {
                                "item_type": "record",
                                "item_id": "file:src/auth/service.py",
                                "item_version": "sha256:service-v2",
                                "code": "temporary_failure",
                                "message": "simulated rejection",
                                "retryable": True,
                            }
                        ],
                    },
                )
            return httpx.Response(500, json={"detail": "unexpected request"})

        with CortexClient(
            base_url="http://cortex.test",
            transport=httpx.MockTransport(reject_first_batch),
        ) as client:
            with self.assertRaises(DevUISyncRejected):
                DevUIConnector(client, self.mapper).sync_snapshot(self.snapshot)

        self.assertEqual(len(paths), 2)
        self.assertFalse(any(path.endswith(":commit") for path in paths))

    def test_reference_package_does_not_import_cortex_core(self) -> None:
        package = Path(__file__).parents[1] / "src" / "cortex_devui"
        source = "\n".join(
            path.read_text(encoding="utf-8") for path in package.glob("*.py")
        )
        self.assertNotIn("data_retrieval", source)

    def test_cli_defaults_source_instance_to_project(self) -> None:
        args = build_parser().parse_args(
            [
                "snapshot.json",
                "--organization-id",
                "org:portfolio",
                "--project-id",
                "project:cortex",
            ]
        )
        config = DevUIConnectorConfig(
            organization_id=args.organization_id,
            project_id=args.project_id,
            source_instance=args.source_instance or args.project_id,
            batch_size=args.batch_size,
        )

        self.assertEqual(config.source_instance, "project:cortex")
        self.assertEqual(args.base_url, "http://127.0.0.1:8765")


@unittest.skipUnless(
    os.getenv("DATA_RETRIEVAL_TEST_POSTGRES_DSN"),
    "DATA_RETRIEVAL_TEST_POSTGRES_DSN is not configured",
)
class PostgreSQLDevUIConnectorTests(unittest.TestCase):
    def test_sdk_to_postgresql_round_trip_preserves_navigation(self) -> None:
        fixture_path = Path(__file__).parents[1] / "evals" / "devui_snapshot_v1.json"
        snapshot = snapshot_from_mapping(
            json.loads(fixture_path.read_text(encoding="utf-8"))
        )
        config = DevUIConnectorConfig(
            organization_id="org:c5-postgres",
            project_id="project:c5-postgres",
            source_instance="project:c5-postgres",
            batch_size=2,
        )
        mapper = DevUIMapper(config)
        app = create_app(
            LocalApiConfig(
                postgres_dsn=os.environ["DATA_RETRIEVAL_TEST_POSTGRES_DSN"]
            )
        )
        with TestClient(app) as api_client:
            with CortexClient(
                base_url="http://cortex.test",
                transport=_ApiTransport(api_client),
            ) as client:
                committed = DevUIConnector(client, mapper).sync_snapshot(snapshot)
                context = client.query(
                    Query(
                        request_id="devui-query:c5-postgres",
                        query="login",
                        scope=config.scope,
                        top_k=10,
                    )
                )

        pointers = mapper.pointers(context)
        self.assertEqual(committed.committed_cursor, "scan:43")
        self.assertTrue(
            any(
                pointer.file == "src/auth/service.py" and pointer.line == 41
                for pointer in pointers
            )
        )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from cortex import (
    ConnectorSyncRejected,
    CortexApiError,
    CortexClient,
    sync_source_batches,
    validate_connector_fixture,
    validate_source_sync,
)
from data_retrieval.connectors import (
    context_pack_from_mapping,
    outcome_from_mapping,
    query_from_mapping,
    sync_batch_from_mapping,
)
from data_retrieval.connectors.codec import (
    source_from_mapping,
    sync_batch_acknowledgement_to_mapping,
    sync_commit_acknowledgement_to_mapping,
    sync_run_to_mapping,
)
from data_retrieval.connectors.contracts import (
    SyncBatchAcknowledgement,
    SyncCommitAcknowledgement,
)


class CortexClientTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture_path = (
            Path(__file__).parents[1] / "evals" / "connector_contract_devui_v1.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        self.source = source_from_mapping(fixture["registration"])
        self.batch = sync_batch_from_mapping(fixture["sync_batch"])
        self.query = query_from_mapping(fixture["query"])
        self.context = context_pack_from_mapping(fixture["context_pack"])
        self.outcome = outcome_from_mapping(fixture["outcome"])

    def test_typed_query_and_outcome_methods_round_trip_public_contracts(self) -> None:
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/v1/queries":
                return httpx.Response(200, json=self._fixture_mapping("context_pack"))
            if request.url.path == "/v1/outcomes":
                return httpx.Response(200, json=json.loads(request.content))
            return httpx.Response(404, json={"detail": "not found"})

        with CortexClient(
            base_url="http://cortex.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            self.assertEqual(client.query(self.query), self.context)
            self.assertEqual(client.report_outcome(self.outcome), self.outcome)

        self.assertEqual([request.url.path for request in requests], [
            "/v1/queries",
            "/v1/outcomes",
        ])

    def _fixture_mapping(self, key: str) -> object:
        fixture_path = (
            Path(__file__).parents[1] / "evals" / "connector_contract_devui_v1.json"
        )
        return json.loads(fixture_path.read_text(encoding="utf-8"))[key]

    def test_typed_sync_session_uses_http_contract_and_commits_explicitly(self) -> None:
        requests: list[httpx.Request] = []
        acknowledged_at = datetime(2026, 9, 19, 14, 0, tzinfo=UTC)

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.path == "/v1/sources":
                return httpx.Response(201, json=json.loads(request.content))
            if request.url.path.endswith("/batches"):
                payload = json.loads(request.content)
                acknowledgement = SyncBatchAcknowledgement(
                    run_request_id=payload["run"]["request_id"],
                    batch_id=payload["batch_id"],
                    sequence=payload["sequence"],
                    acknowledged_at=acknowledged_at,
                    accepted_records=len(payload["records"]),
                    accepted_relations=len(payload["relations"]),
                    accepted_tombstones=len(payload["tombstones"]),
                )
                return httpx.Response(
                    200,
                    json=sync_batch_acknowledgement_to_mapping(acknowledgement),
                )
            if request.url.path.endswith(":commit"):
                payload = json.loads(request.content)
                acknowledgement = SyncCommitAcknowledgement(
                    request_id=payload["request_id"],
                    run_request_id=self.batch.run.request_id,
                    source=self.batch.run.source,
                    committed_cursor=self.batch.run.proposed_cursor or "",
                    committed_at=acknowledged_at,
                )
                return httpx.Response(
                    200,
                    json=sync_commit_acknowledgement_to_mapping(acknowledgement),
                )
            if request.method == "GET" and "/v1/sync-runs/" in request.url.path:
                return httpx.Response(200, json=sync_run_to_mapping(self.batch.run))
            return httpx.Response(404, json={"detail": "not found"})

        with CortexClient(
            base_url="http://cortex.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            self.assertEqual(client.register_source(self.source), self.source)
            with client.sync(self.batch.run) as sync:
                accepted = sync.submit(
                    batch_id=self.batch.batch_id,
                    records=self.batch.records,
                    relations=self.batch.relations,
                    tombstones=self.batch.tombstones,
                )
            self.assertTrue(accepted.complete)
            self.assertFalse(sync.committed)
            self.assertEqual(client.get_sync_run(self.batch.run.request_id), self.batch.run)
            committed = sync.commit(request_id="devui-sync:scan-42:commit")

        self.assertEqual(committed.committed_cursor, "scan:42")
        self.assertTrue(sync.committed)
        self.assertEqual(
            [request.method for request in requests],
            ["POST", "POST", "GET", "POST"],
        )

    def test_structured_error_exposes_retry_guidance_without_retrying(self) -> None:
        calls = 0

        def handler(_request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                429,
                json={"detail": "rate limited"},
                headers={"Retry-After": "5"},
            )

        with CortexClient(
            base_url="http://cortex.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            with self.assertRaises(CortexApiError) as raised:
                client.register_source(self.source)

        self.assertEqual(calls, 1)
        self.assertEqual(str(raised.exception), "rate limited")
        self.assertEqual(raised.exception.status_code, 429)
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.retry_after, "5")

    def test_shared_sync_helper_commits_only_after_complete_batches(self) -> None:
        paths: list[str] = []
        acknowledged_at = datetime(2026, 9, 20, 14, 0, tzinfo=UTC)

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path == "/v1/sources":
                return httpx.Response(201, json=json.loads(request.content))
            if request.url.path.endswith("/batches"):
                payload = json.loads(request.content)
                acknowledgement = SyncBatchAcknowledgement(
                    run_request_id=payload["run"]["request_id"],
                    batch_id=payload["batch_id"],
                    sequence=payload["sequence"],
                    acknowledged_at=acknowledged_at,
                    accepted_records=len(payload["records"]),
                    accepted_relations=len(payload["relations"]),
                    accepted_tombstones=len(payload["tombstones"]),
                )
                return httpx.Response(
                    200,
                    json=sync_batch_acknowledgement_to_mapping(acknowledgement),
                )
            if request.url.path.endswith(":commit"):
                payload = json.loads(request.content)
                acknowledgement = SyncCommitAcknowledgement(
                    request_id=payload["request_id"],
                    run_request_id=self.batch.run.request_id,
                    source=self.batch.run.source,
                    committed_cursor=self.batch.run.proposed_cursor or "",
                    committed_at=acknowledged_at,
                )
                return httpx.Response(
                    200,
                    json=sync_commit_acknowledgement_to_mapping(acknowledgement),
                )
            return httpx.Response(404, json={"detail": "not found"})

        with CortexClient(
            base_url="http://cortex.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            committed = sync_source_batches(
                client,
                source=self.source,
                batches=(self.batch,),
                commit_request_id="devui-sync:scan-42:helper-commit",
            )

        self.assertEqual(committed.committed_cursor, "scan:42")
        self.assertEqual(len(paths), 3)
        self.assertTrue(paths[-1].endswith(":commit"))

    def test_shared_sync_helper_withholds_commit_after_rejection(self) -> None:
        paths: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            paths.append(request.url.path)
            if request.url.path == "/v1/sources":
                return httpx.Response(201, json=json.loads(request.content))
            if request.url.path.endswith("/batches"):
                payload = json.loads(request.content)
                return httpx.Response(
                    200,
                    json={
                        "run_request_id": payload["run"]["request_id"],
                        "batch_id": payload["batch_id"],
                        "sequence": payload["sequence"],
                        "acknowledged_at": "2026-09-20T14:00:00+00:00",
                        "accepted_records": 0,
                        "accepted_relations": 0,
                        "accepted_tombstones": 0,
                        "failures": [
                            {
                                "item_type": "record",
                                "item_id": self.batch.records[0].ref.external_id,
                                "item_version": (
                                    self.batch.records[0].ref.external_version
                                ),
                                "code": "temporary_failure",
                                "message": "simulated rejection",
                                "retryable": True,
                            }
                        ],
                    },
                )
            return httpx.Response(500, json={"detail": "commit must not be called"})

        with CortexClient(
            base_url="http://cortex.test",
            transport=httpx.MockTransport(handler),
        ) as client:
            with self.assertRaises(ConnectorSyncRejected):
                sync_source_batches(
                    client,
                    source=self.source,
                    batches=(self.batch,),
                    commit_request_id="must-not-commit",
                )

        self.assertEqual(len(paths), 2)
        self.assertFalse(any(path.endswith(":commit") for path in paths))


class ConnectorContractTestKitTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture_root = Path(__file__).parents[1] / "evals"
        self.fixtures = tuple(
            json.loads(path.read_text(encoding="utf-8"))
            for path in (
                fixture_root / "connector_contract_devui_v1.json",
                fixture_root / "connector_contract_slack_v1.json",
            )
        )

    def test_reference_fixtures_validate_without_cortex_internals(self) -> None:
        reports = tuple(validate_connector_fixture(fixture) for fixture in self.fixtures)

        self.assertEqual([report.batch_count for report in reports], [1, 1])
        self.assertGreater(sum(report.record_count for report in reports), 0)
        self.assertGreater(sum(report.relation_count for report in reports), 0)
        self.assertGreater(sum(report.tombstone_count for report in reports), 0)

    def test_non_contiguous_batch_sequences_are_rejected(self) -> None:
        fixture = self.fixtures[0]
        source = source_from_mapping(fixture["registration"])
        batch = sync_batch_from_mapping(fixture["sync_batch"])

        with self.assertRaisesRegex(ValueError, "contiguous from zero"):
            validate_source_sync(source, (replace(batch, sequence=1),))


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import httpx

from cortex import (
    CortexApiError,
    CortexClient,
    validate_connector_fixture,
    validate_source_sync,
)
from data_retrieval.connectors import sync_batch_from_mapping
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

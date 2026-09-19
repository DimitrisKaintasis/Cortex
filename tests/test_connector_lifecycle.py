from __future__ import annotations

import json
import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast
from uuid import uuid4

from data_retrieval.connectors import (
    RecordPayload,
    SourceRef,
    SyncBatch,
    sync_batch_from_mapping,
    sync_batch_to_mapping,
)
from data_retrieval.connectors.codec import source_from_mapping
from data_retrieval.services.connector_sync import ConnectorSyncService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.postgresql import PostgreSQLRepository
from data_retrieval.storage.repository import ConnectorLifecycleRepository
from data_retrieval.storage.sqlite import SQLiteRepository


class _Clock:
    def __init__(self) -> None:
        self.value = datetime(2026, 9, 19, 12, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        value = self.value
        self.value += timedelta(seconds=1)
        return value


class _ConnectorLifecycleBehavior:
    def setUp(self) -> None:
        fixture_root = Path(__file__).parents[1] / "evals"
        self._identity_suffix = uuid4().hex
        self.devui = cast(
            dict[str, object],
            self._isolate(self._load(fixture_root / "connector_contract_devui_v1.json")),
        )
        self.slack = cast(
            dict[str, object],
            self._isolate(self._load(fixture_root / "connector_contract_slack_v1.json")),
        )
        self.repository = self.make_repository()
        self.service = ConnectorSyncService(self.repository, clock=_Clock())

    def make_repository(self) -> ConnectorLifecycleRepository:
        raise NotImplementedError

    @staticmethod
    def _load(path: Path) -> dict[str, object]:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value

    def _isolate(self, value: object) -> object:
        if isinstance(value, dict):
            return {
                key: (
                    f"{item}:{self._identity_suffix}"
                    if key in {"source_instance", "request_id", "batch_id"}
                    and isinstance(item, str)
                    else self._isolate(item)
                )
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [self._isolate(item) for item in value]
        return value

    def _request_id(self, value: str) -> str:
        return f"{value}:{self._identity_suffix}"

    def test_batch_mapping_is_canonical_and_round_trips(self) -> None:
        batch = sync_batch_from_mapping(self.slack["sync_batch"])

        self.assertEqual(sync_batch_from_mapping(sync_batch_to_mapping(batch)), batch)

    def test_versions_lineage_relations_tombstones_and_commit_are_replay_safe(self) -> None:
        source = source_from_mapping(self.slack["registration"])
        batch = sync_batch_from_mapping(self.slack["sync_batch"])
        batch = replace(batch, run=replace(batch.run, previous_cursor=None))
        self.service.register_source(source)

        first = self.service.submit_batch(batch)
        replay = self.service.submit_batch(batch)

        self.assertEqual(first, replay)
        self.assertTrue(first.complete)
        self.assertEqual(first.accepted_records, 3)
        current = self.service.get_current_record(
            source=source.source,
            external_id="channel:C-AUTH:message:100.0001",
        )
        self.assertIsNotNone(current)
        assert current is not None
        self.assertEqual(current.ref.external_version, "edited:100.0005")
        self.assertEqual(
            self.service.get_record_predecessor(current.ref),
            batch.records[0].ref,
        )
        self.assertEqual(
            self.service.get_relation(
                source=source.source,
                relation_id=batch.relations[0].relation_id,
                relation_version=batch.relations[0].relation_version,
            ),
            batch.relations[0],
        )
        self.assertIsNone(
            self.service.get_current_record(
                source=source.source,
                external_id=batch.tombstones[0].record.external_id,
            )
        )

        committed = self.service.commit(
            request_id=self._request_id("slack-sync:event-page-18:commit"),
            run_request_id=batch.run.request_id,
        )
        committed_replay = self.service.commit(
            request_id=self._request_id("slack-sync:event-page-18:commit"),
            run_request_id=batch.run.request_id,
        )
        self.assertEqual(committed, committed_replay)
        self.assertEqual(self.service.get_cursor(source.source), "event-page:18")

    def test_source_batch_and_commit_identity_conflicts_are_rejected(self) -> None:
        source = source_from_mapping(self.devui["registration"])
        batch = sync_batch_from_mapping(self.devui["sync_batch"])
        self.service.register_source(source)
        with self.assertRaisesRegex(ValueError, "registration conflicts"):
            self.service.register_source(replace(source, connector_version="0.2.0"))

        self.service.submit_batch(batch)
        changed_record = replace(
            batch.records[0],
            payload=RecordPayload(inline="different content for the same version"),
        )
        conflicting = replace(batch, records=(changed_record, *batch.records[1:]))
        with self.assertRaisesRegex(ValueError, "different payload"):
            self.service.submit_batch(conflicting)

        self.service.commit(
            request_id=self._request_id("devui-sync:scan-42:commit"),
            run_request_id=batch.run.request_id,
        )
        with self.assertRaisesRegex(ValueError, "already committed"):
            self.service.commit(
                request_id=self._request_id("devui-sync:scan-42:second-commit"),
                run_request_id=batch.run.request_id,
            )

    def test_missing_relation_endpoint_is_item_failure_and_blocks_cursor_commit(self) -> None:
        source = source_from_mapping(self.devui["registration"])
        original = sync_batch_from_mapping(self.devui["sync_batch"])
        self.service.register_source(source)
        missing_target = replace(
            original.relations[0].target,
            external_id="missing:record",
            external_version="missing:1",
        )
        relation = replace(original.relations[0], target=missing_target)
        batch = SyncBatch(
            batch_id=self._request_id("missing-relation:batch:0"),
            sequence=0,
            run=replace(
                original.run,
                request_id=self._request_id("missing-relation:run"),
                proposed_cursor="missing-relation:cursor",
            ),
            relations=(relation,),
        )

        acknowledgement = self.service.submit_batch(batch)

        self.assertFalse(acknowledgement.complete)
        self.assertEqual(acknowledgement.failures[0].code, "missing_endpoint")
        self.assertTrue(acknowledgement.failures[0].retryable)
        with self.assertRaisesRegex(ValueError, "item failures"):
            self.service.commit(
                request_id=self._request_id("missing-relation:commit"),
                run_request_id=batch.run.request_id,
            )

        source_record = next(
            record for record in original.records if record.ref == relation.source
        )
        target_record = replace(source_record, ref=missing_target)
        repair = SyncBatch(
            batch_id=self._request_id("missing-relation:repair:1"),
            sequence=1,
            run=batch.run,
            records=(source_record, target_record),
            relations=(relation,),
        )
        repaired = self.service.submit_batch(repair)
        committed = self.service.commit(
            request_id=self._request_id("missing-relation:commit"),
            run_request_id=batch.run.request_id,
        )

        self.assertTrue(repaired.complete)
        self.assertEqual(committed.committed_cursor, "missing-relation:cursor")

    def test_incremental_run_requires_the_committed_previous_cursor(self) -> None:
        source = source_from_mapping(self.slack["registration"])
        batch = sync_batch_from_mapping(self.slack["sync_batch"])
        self.service.register_source(source)

        with self.assertRaisesRegex(ValueError, "previous_cursor"):
            self.service.submit_batch(batch)

    def test_non_retryable_version_conflict_keeps_cursor_blocked(self) -> None:
        source = source_from_mapping(self.devui["registration"])
        batch = sync_batch_from_mapping(self.devui["sync_batch"])
        self.service.register_source(source)
        self.service.submit_batch(batch)
        changed_record = replace(
            batch.records[0],
            payload=RecordPayload(inline="conflicting content for a durable version"),
        )
        conflicting = replace(
            batch,
            batch_id=self._request_id("version-conflict:batch:1"),
            sequence=1,
            records=(changed_record,),
            relations=(),
        )

        acknowledgement = self.service.submit_batch(conflicting)

        self.assertFalse(acknowledgement.complete)
        self.assertFalse(acknowledgement.failures[0].retryable)
        with self.assertRaisesRegex(ValueError, "item failures"):
            self.service.commit(
                request_id=self._request_id("version-conflict:commit"),
                run_request_id=batch.run.request_id,
            )

    def test_item_scope_cannot_escape_registered_owner(self) -> None:
        source = source_from_mapping(self.devui["registration"])
        batch = sync_batch_from_mapping(self.devui["sync_batch"])
        escaped_scope = replace(batch.records[0].scope, project_id="project:other")
        escaped = replace(
            batch,
            records=(replace(batch.records[0], scope=escaped_scope), *batch.records[1:]),
        )
        self.service.register_source(source)

        with self.assertRaisesRegex(ValueError, "outside source owner project_id"):
            self.service.submit_batch(escaped)

    def test_unknown_source_is_rejected(self) -> None:
        batch = sync_batch_from_mapping(self.devui["sync_batch"])

        with self.assertRaisesRegex(ValueError, "not registered"):
            self.service.submit_batch(batch)
        self.assertIsNone(
            self.service.get_current_record(
                source=SourceRef("devui", "project:cortex"),
                external_id="module:authentication",
            )
        )


class InMemoryConnectorLifecycleTests(_ConnectorLifecycleBehavior, unittest.TestCase):
    def make_repository(self) -> ConnectorLifecycleRepository:
        return InMemoryRepository()


class SQLiteConnectorLifecycleTests(_ConnectorLifecycleBehavior, unittest.TestCase):
    def setUp(self) -> None:
        self._temporary_directory = tempfile.TemporaryDirectory()
        self._database_path = (
            Path(self._temporary_directory.name) / "connector-lifecycle.sqlite3"
        )
        super().setUp()

    def tearDown(self) -> None:
        assert isinstance(self.repository, SQLiteRepository)
        self.repository.close()
        self._temporary_directory.cleanup()

    def make_repository(self) -> ConnectorLifecycleRepository:
        return SQLiteRepository(self._database_path)

    def test_lifecycle_state_survives_reopen(self) -> None:
        source = source_from_mapping(self.devui["registration"])
        batch = sync_batch_from_mapping(self.devui["sync_batch"])
        self.service.register_source(source)
        acknowledgement = self.service.submit_batch(batch)
        assert isinstance(self.repository, SQLiteRepository)
        self.repository.close()

        self.repository = SQLiteRepository(self._database_path)
        self.service = ConnectorSyncService(self.repository, clock=_Clock())

        self.assertEqual(self.service.submit_batch(batch), acknowledgement)
        self.assertEqual(
            self.service.get_current_record(
                source=source.source,
                external_id=batch.records[0].ref.external_id,
            ),
            batch.records[0],
        )
        committed = self.service.commit(
            request_id=self._request_id("devui-sync:scan-42:commit"),
            run_request_id=batch.run.request_id,
        )
        self.assertEqual(committed.committed_cursor, "scan:42")


@unittest.skipUnless(
    os.getenv("DATA_RETRIEVAL_TEST_POSTGRES_DSN"),
    "DATA_RETRIEVAL_TEST_POSTGRES_DSN is not configured",
)
class PostgreSQLConnectorLifecycleTests(_ConnectorLifecycleBehavior, unittest.TestCase):
    def make_repository(self) -> ConnectorLifecycleRepository:
        return PostgreSQLRepository(os.environ["DATA_RETRIEVAL_TEST_POSTGRES_DSN"])

    def tearDown(self) -> None:
        assert isinstance(self.repository, PostgreSQLRepository)
        self.repository.close()


if __name__ == "__main__":
    unittest.main()

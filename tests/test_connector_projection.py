from __future__ import annotations

import os
import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from data_retrieval.connectors.contracts import (
    ConnectorCapability,
    ContributionPolicy,
    Outcome,
    OutcomeValue,
    Query,
    Record,
    RecordModality,
    RecordPayload,
    RecordRef,
    Scope,
    Source,
    SourceRef,
    SyncBatch,
    SyncMode,
    SyncRun,
    Tombstone,
    Visibility,
)
from data_retrieval.connectors.projection import scope_namespace
from data_retrieval.domain.models import AtomLinkRelation
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.connector_access import ConnectorAccessService
from data_retrieval.services.connector_sync import ConnectorSyncService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.postgresql import PostgreSQLRepository
from data_retrieval.storage.sqlite import SQLiteRepository

NOW = datetime(2026, 9, 20, 8, 0, tzinfo=UTC)


def _scope() -> Scope:
    return Scope(
        visibility=Visibility.PROJECT,
        organization_id="org:test",
        project_id="project:test",
        contribution_policy=ContributionPolicy.PRIVATE,
    )


def _source() -> Source:
    return Source(
        source=SourceRef("test", "projection"),
        connector_id="test-connector",
        connector_version="1.0.0",
        owner_scope=Scope(
            visibility=Visibility.ORGANIZATION,
            organization_id="org:test",
        ),
        capabilities=(ConnectorCapability.SOURCE_SYNC,),
    )


def _record(version: str, text: str, *, minute: int) -> Record:
    source = _source().source
    return Record(
        ref=RecordRef(source, "item:1", version),
        modality=RecordModality.TEXT,
        payload=RecordPayload(inline=text),
        scope=_scope(),
        observed_at=NOW.replace(minute=minute),
        explicit_tags=("storage",),
    )


def _run() -> SyncRun:
    return SyncRun(
        request_id="run:projection",
        source=_source().source,
        mode=SyncMode.FULL,
        started_at=NOW,
        proposed_cursor="cursor:1",
    )


class ConnectorProjectionTests(unittest.TestCase):
    def test_query_returns_external_evidence_and_outcome_is_attributable(self) -> None:
        repository = InMemoryRepository()
        sync = ConnectorSyncService(repository)
        sync.register_source(_source())
        record = _record("v1", "Keep sessions in Redis.", minute=1)
        sync.submit_batch(SyncBatch("batch:query", 0, _run(), records=(record,)))
        access = ConnectorAccessService(repository)
        query = Query(request_id="query:1", query="Redis sessions", scope=_scope())

        context = access.query(query)

        self.assertEqual(access.query(query), context)
        self.assertTrue(context.items)
        self.assertEqual(context.items[0].record, record.ref)
        projection = repository.get_connector_record_projection(record.ref)
        assert projection is not None
        self.assertNotIn(context.items[0].evidence_id, projection.atom_ids)
        with self.assertRaisesRegex(ValueError, "query request identity"):
            access.query(replace(query, query="different payload"))

        invalid = Outcome(
            request_id="outcome:invalid",
            retrieval_id=context.retrieval_id,
            used_evidence_ids=("evidence:not-returned",),
            outcome=OutcomeValue.POSITIVE,
            occurred_at=NOW,
        )
        with self.assertRaisesRegex(ValueError, "returned by this retrieval"):
            access.report_outcome(invalid)

        outcome = replace(
            invalid,
            request_id="outcome:1",
            used_evidence_ids=(context.items[0].evidence_id,),
            reason="The answer used this evidence.",
        )
        self.assertEqual(access.report_outcome(outcome), outcome)
        self.assertEqual(access.report_outcome(outcome), outcome)
        with self.assertRaisesRegex(ValueError, "outcome request identity"):
            access.report_outcome(replace(outcome, reason="different payload"))

    def test_versions_project_with_supersedes_links_and_tombstones_leave_serving(
        self,
    ) -> None:
        for repository in (InMemoryRepository(), self._sqlite_repository()):
            with self.subTest(adapter=type(repository).__name__):
                try:
                    service = ConnectorSyncService(repository)
                    service.register_source(_source())
                    old = _record("v1", "Keep sessions in Redis.", minute=1)
                    new = _record("v2", "Move sessions to PostgreSQL.", minute=2)
                    service.submit_batch(
                        SyncBatch("batch:1", 0, _run(), records=(old,))
                    )
                    service.submit_batch(
                        SyncBatch("batch:2", 1, _run(), records=(new,))
                    )

                    old_projection = repository.get_connector_record_projection(old.ref)
                    new_projection = repository.get_connector_record_projection(new.ref)
                    self.assertIsNotNone(old_projection)
                    self.assertIsNotNone(new_projection)
                    assert old_projection is not None and new_projection is not None
                    links = repository.get_atom_links(new_projection.atom_ids[0])
                    self.assertTrue(
                        any(
                            link.relation is AtomLinkRelation.SUPERSEDES
                            and link.to_atom_id in old_projection.atom_ids
                            for link in links
                        )
                    )

                    before = RetrievalService(repository).retrieve(
                        QueryPlan(
                            query="PostgreSQL",
                            namespace=scope_namespace(_scope()),
                            temporal_mode=TemporalMode.NONE,
                        )
                    )
                    self.assertTrue(before.items)

                    tombstone = Tombstone(
                        record=RecordRef(_source().source, "item:1"),
                        tombstone_version="deleted:1",
                        observed_at=NOW.replace(minute=3),
                        reason="source_deleted",
                    )
                    batch = SyncBatch(
                        "batch:3", 2, _run(), tombstones=(tombstone,)
                    )
                    acknowledgement = service.submit_batch(batch)
                    self.assertEqual(service.submit_batch(batch), acknowledgement)
                    after = RetrievalService(repository).retrieve(
                        QueryPlan(
                            query="PostgreSQL",
                            namespace=scope_namespace(_scope()),
                            temporal_mode=TemporalMode.NONE,
                        )
                    )
                    self.assertFalse(after.items)
                    committed = service.commit(
                        request_id="commit:projection",
                        run_request_id=_run().request_id,
                    )
                    self.assertEqual(committed.committed_cursor, "cursor:1")
                finally:
                    if isinstance(repository, SQLiteRepository):
                        repository.close()

    def test_projection_failure_blocks_commit_and_exact_replay_repairs_it(self) -> None:
        repository = _FailFirstProjectionRepository()
        service = ConnectorSyncService(repository)
        service.register_source(_source())
        batch = SyncBatch("batch:failure", 0, _run(), records=(_record("v1", "data", minute=1),))

        with self.assertRaisesRegex(RuntimeError, "simulated projection failure"):
            service.submit_batch(batch)
        with self.assertRaisesRegex(ValueError, "unprojected"):
            service.commit(
                request_id="commit:failure", run_request_id=batch.run.request_id
            )

        service.submit_batch(batch)
        committed = service.commit(
            request_id="commit:failure", run_request_id=batch.run.request_id
        )
        self.assertEqual(committed.committed_cursor, "cursor:1")

    def test_outcome_replay_repairs_a_receipt_failure_without_learning_twice(self) -> None:
        repository = _FailFirstOutcomeReceiptRepository()
        sync = ConnectorSyncService(repository)
        sync.register_source(_source())
        record = _record("v1", "Keep sessions in Redis.", minute=1)
        sync.submit_batch(SyncBatch("batch:outcome", 0, _run(), records=(record,)))
        access = ConnectorAccessService(repository)
        context = access.query(
            Query(request_id="query:outcome", query="Redis", scope=_scope())
        )
        outcome = Outcome(
            request_id="outcome:receipt-failure",
            retrieval_id=context.retrieval_id,
            used_evidence_ids=(context.items[0].evidence_id,),
            outcome=OutcomeValue.POSITIVE,
            occurred_at=NOW,
        )

        with self.assertRaisesRegex(RuntimeError, "simulated outcome receipt failure"):
            access.report_outcome(outcome)

        self.assertIsNotNone(repository.get_feedback_event(outcome.request_id))
        self.assertEqual(access.report_outcome(outcome), outcome)

    def test_referenced_payload_is_rejected_before_the_run_becomes_durable(self) -> None:
        repository = InMemoryRepository()
        service = ConnectorSyncService(repository)
        service.register_source(_source())
        record = replace(
            _record("v1", "placeholder", minute=1),
            payload=RecordPayload(
                reference_uri="https://example.test/payload",
                content_hash="sha256:test",
            ),
        )

        with self.assertRaisesRegex(ValueError, "inline content"):
            service.submit_batch(
                SyncBatch("batch:reference", 0, _run(), records=(record,))
            )

        self.assertIsNone(service.get_run(_run().request_id))

    def _sqlite_repository(self) -> SQLiteRepository:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        return SQLiteRepository(Path(directory.name) / "projection.sqlite3")


class _FailFirstProjectionRepository(InMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self._fail_projection = True

    def store_connector_record_projection(self, projection):
        if self._fail_projection:
            self._fail_projection = False
            raise RuntimeError("simulated projection failure")
        return super().store_connector_record_projection(projection)


class _FailFirstOutcomeReceiptRepository(InMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self._fail_outcome_receipt = True

    def store_connector_outcome_receipt(self, *, fingerprint, outcome):
        if self._fail_outcome_receipt:
            self._fail_outcome_receipt = False
            raise RuntimeError("simulated outcome receipt failure")
        return super().store_connector_outcome_receipt(
            fingerprint=fingerprint, outcome=outcome
        )


@unittest.skipUnless(
    os.getenv("DATA_RETRIEVAL_TEST_POSTGRES_DSN"),
    "DATA_RETRIEVAL_TEST_POSTGRES_DSN is not configured",
)
class PostgreSQLConnectorAccessTests(unittest.TestCase):
    def test_query_and_outcome_receipts_are_replay_safe(self) -> None:
        with PostgreSQLRepository(
            os.environ["DATA_RETRIEVAL_TEST_POSTGRES_DSN"]
        ) as repository:
            sync = ConnectorSyncService(repository)
            sync.register_source(_source())
            record = _record("v1", "Keep sessions in Redis.", minute=1)
            sync.submit_batch(
                SyncBatch("batch:postgres-query", 0, _run(), records=(record,))
            )
            access = ConnectorAccessService(repository)
            query = Query(
                request_id="query:postgres", query="Redis sessions", scope=_scope()
            )
            context = access.query(query)
            outcome = Outcome(
                request_id="outcome:postgres",
                retrieval_id=context.retrieval_id,
                used_evidence_ids=(context.items[0].evidence_id,),
                outcome=OutcomeValue.POSITIVE,
                occurred_at=NOW,
            )

            self.assertEqual(access.query(query), context)
            self.assertEqual(access.report_outcome(outcome), outcome)
            self.assertEqual(access.report_outcome(outcome), outcome)


if __name__ == "__main__":
    unittest.main()

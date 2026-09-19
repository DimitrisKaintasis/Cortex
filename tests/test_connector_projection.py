from __future__ import annotations

import tempfile
import unittest
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from data_retrieval.connectors.contracts import (
    ConnectorCapability,
    ContributionPolicy,
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
from data_retrieval.services.connector_sync import ConnectorSyncService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.memory import InMemoryRepository
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


if __name__ == "__main__":
    unittest.main()

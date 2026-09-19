from __future__ import annotations

import sqlite3
import tempfile
import unittest
from dataclasses import replace
from datetime import timedelta
from pathlib import Path

from data_retrieval.domain.models import (
    CalibrationTarget,
    WeightEvent,
    WeightEventSource,
    utc_now,
)
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.sqlite import SQLiteRepository


class WeightLedgerTests(unittest.TestCase):
    def test_reconstruction_allows_a_valid_history_to_revisit_a_weight(self) -> None:
        started_at = utc_now()
        events = tuple(
            WeightEvent(
                event_id=f"event-{index}",
                namespace="project-a",
                target_type=CalibrationTarget.ATOM_TAG,
                target_id="atom-1",
                related_id="tag-1",
                relation_type="has_tag",
                source_type=WeightEventSource.FEEDBACK,
                source_id=f"feedback-{index}",
                policy_version="test-v1",
                weight_before=before,
                weight_after=after,
                delta=after - before,
                created_at=started_at + timedelta(microseconds=index),
            )
            for index, (before, after) in enumerate(
                ((0.0, 1.0), (1.0, 1.1), (1.1, 1.0), (1.0, 1.1))
            )
        )

        reconstructed, issues = WeightLedgerService._reconstruct(events)

        self.assertAlmostEqual(reconstructed, 1.1)
        self.assertEqual(issues, ())

    def test_ingestion_calibration_and_feedback_form_one_replayable_chain(self) -> None:
        repository = InMemoryRepository()
        ingested = IngestService(repository).ingest_text(
            namespace="project-a",
            source="architecture",
            text="PostgreSQL stores the retrieval architecture.",
            explicit_tags=("architecture", "postgresql"),
        )
        retrieved = RetrievalService(repository).retrieve(
            QueryPlan(
                query="Which architecture is used?",
                namespace="project-a",
                query_tags=("architecture",),
                top_k=1,
            )
        )
        LearningService(repository).apply_feedback(
            FeedbackRequest(
                feedback_id="feedback-1",
                retrieval_id=retrieved.retrieval_id,
                selected_atom_ids=(ingested.atom_ids[0],),
                outcome="positive",
                reason="used",
            )
        )

        audit = WeightLedgerService(repository).audit_namespace("project-a")
        atom_tag_events = repository.list_weight_events(
            namespace="project-a",
            target_type=CalibrationTarget.ATOM_TAG,
            target_id=ingested.atom_ids[0],
        )
        all_events = repository.list_weight_events(namespace="project-a")

        self.assertTrue(audit.passed)
        self.assertEqual(
            {event.source_type for event in atom_tag_events},
            {
                WeightEventSource.INGESTION,
                WeightEventSource.FEEDBACK,
            },
        )
        self.assertIn(
            WeightEventSource.CALIBRATION,
            {event.source_type for event in all_events},
        )

    def test_audit_detects_and_repairs_only_the_serving_aggregate(self) -> None:
        repository = InMemoryRepository()
        ingested = IngestService(repository).ingest_text(
            namespace="project-a",
            source="architecture",
            text="A canonical tagged atom.",
            explicit_tags=("architecture",),
        )
        edge = repository.atom_tags_for(ingested.atom_ids[0])[0]
        repository.restore_weight_aggregates(
            atom_tags=(replace(edge, weight_raw=edge.weight_raw + 3.0),),
            atom_links=(),
            tag_relations=(),
        )

        before = WeightLedgerService(repository).audit_namespace("project-a")
        after = WeightLedgerService(repository).repair_aggregates("project-a")

        self.assertEqual(before.mismatched_target_count, 1)
        self.assertTrue(after.passed)
        self.assertAlmostEqual(
            repository.atom_tags_for(ingested.atom_ids[0])[0].weight_raw,
            edge.weight_raw,
        )

    def test_sqlite_reopen_does_not_duplicate_existing_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ledger.sqlite3"
            with SQLiteRepository(path) as repository:
                IngestService(repository).ingest_text(
                    namespace="project-a",
                    source="architecture",
                    text="A durable tagged atom.",
                    explicit_tags=("architecture",),
                )
                first_count = len(
                    repository.list_weight_events(namespace="project-a")
                )

            with SQLiteRepository(path) as reopened:
                second_count = len(
                    reopened.list_weight_events(namespace="project-a")
                )
                audit = WeightLedgerService(reopened).audit_namespace("project-a")

            self.assertEqual(second_count, first_count)
            self.assertTrue(audit.passed)

    def test_pre_ledger_sqlite_state_gets_an_explicit_migration_baseline(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy-ledger.sqlite3"
            with SQLiteRepository(path) as repository:
                IngestService(repository).ingest_text(
                    namespace="project-a",
                    source="architecture",
                    text="An existing tagged atom.",
                    explicit_tags=("architecture",),
                )

            connection = sqlite3.connect(path)
            connection.execute("DELETE FROM weight_events")
            connection.executescript(
                """
                DROP TABLE connector_outcome_receipts;
                DROP TABLE connector_query_receipts;
                DROP TABLE connector_tombstone_projections;
                DROP TABLE connector_projection_atoms;
                DROP TABLE connector_record_projections;
                DROP TABLE connector_sync_batches;
                DROP TABLE connector_sync_runs;
                DROP TABLE connector_relations;
                DROP TABLE connector_tombstones;
                DROP TABLE connector_records;
                DROP TABLE connector_record_objects;
                DROP TABLE connector_sources;
                """
            )
            connection.execute("PRAGMA user_version = 0")
            connection.commit()
            connection.close()

            with SQLiteRepository(path) as reopened:
                events = reopened.list_weight_events(namespace="project-a")
                audit = WeightLedgerService(reopened).audit_namespace("project-a")

            self.assertTrue(events)
            self.assertEqual(
                {event.source_type for event in events},
                {WeightEventSource.MIGRATION},
            )
            self.assertTrue(audit.passed)


if __name__ == "__main__":
    unittest.main()

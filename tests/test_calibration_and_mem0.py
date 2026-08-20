from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from data_retrieval.calibration import TeacherCalibrationService
from data_retrieval.domain.models import AtomLinkRelation
from data_retrieval.ingestion.chunker import TextChunker
from data_retrieval.mem0 import (
    Mem0BootstrapService,
    Mem0ImportService,
    Mem0PythonProcessor,
    Mem0Record,
    load_mem0_records,
)
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.sqlite import SQLiteRepository


class _FakeMem0Processor:
    def __init__(
        self,
        results: tuple[dict[str, object], ...],
        *,
        profile_id: str = "test-profile-v1",
    ) -> None:
        self.results = results
        self.profile_id = profile_id
        self.calls: list[dict[str, object]] = []

    def add(
        self,
        messages: tuple[dict[str, str], ...],
        *,
        user_id: str,
        run_id: str,
        metadata: dict[str, object],
    ) -> tuple[dict[str, object], ...]:
        self.calls.append(
            {
                "messages": messages,
                "user_id": user_id,
                "run_id": run_id,
                "metadata": metadata,
            }
        )
        return self.results


class CalibrationAndMem0Tests(unittest.TestCase):
    def test_mem0_fact_objects_are_normalized_for_small_models(self) -> None:
        response = json.dumps(
            {
                "facts": [
                    {"fact": "The event lasted two hours."},
                    "The meeting is Tuesday.",
                    {"unsupported": "ignored"},
                ]
            }
        )

        normalized = Mem0PythonProcessor._normalize_fact_response(response)

        self.assertEqual(
            json.loads(normalized),
            {
                "facts": [
                    "The event lasted two hours.",
                    "The meeting is Tuesday.",
                ]
            },
        )

    def test_mem0_bootstrap_processes_documents_chronologically(self) -> None:
        repository = InMemoryRepository()
        IngestService(repository).ingest_text(
            namespace="project-a",
            source="recent",
            text="Recent state",
            occurred_at=datetime(2026, 8, 20, tzinfo=UTC),
        )
        IngestService(repository).ingest_text(
            namespace="project-a",
            source="old",
            text="Old state",
            occurred_at=datetime(2025, 8, 20, tzinfo=UTC),
        )
        processor = _FakeMem0Processor(
            ({"id": "memory-1", "memory": "A distilled state."},)
        )

        Mem0BootstrapService(repository, processor).run(
            namespace="project-a", max_documents=1
        )

        sent_messages = processor.calls[0]["messages"]
        self.assertEqual(sent_messages[0]["content"], "Old state")
        self.assertIsNone(processor.calls[0]["run_id"])

    def test_existing_atoms_can_be_bootstrapped_through_mem0_once(self) -> None:
        repository = InMemoryRepository()
        ingested = IngestService(
            repository, chunker=TextChunker(max_chars=100, overlap_chars=0)
        ).ingest_text(
            namespace="project-a",
            source="conversation",
            text=(
                "User prefers PostgreSQL for durable storage.\n\n"
                "Assistant recommends keeping the Mac Mini stateless."
            ),
            explicit_tags=("architecture",),
            metadata={"expected_answer": "must-not-leak", "evidence_atom_ids": ["secret"]},
        )
        processor = _FakeMem0Processor(
            ({"id": "remote-1", "memory": "PostgreSQL stores the durable project data."},)
        )
        service = Mem0BootstrapService(
            repository,
            processor,
            atom_batch_size=10,
        )

        first = service.run(namespace="project-a")
        second = service.run(namespace="project-a")

        self.assertEqual(first.batches_processed, 1)
        self.assertEqual(first.memories_imported, 1)
        self.assertEqual(first.source_lineage_links_created, len(ingested.atom_ids))
        self.assertGreater(first.calibration_signals_created, len(ingested.atom_ids))
        self.assertEqual(second.batches_resumed, 1)
        self.assertEqual(second.batches_processed, 0)
        self.assertEqual(len(processor.calls), 1)
        sent_metadata = processor.calls[0]["metadata"]
        self.assertNotIn("expected_answer", sent_metadata)
        self.assertNotIn("evidence_atom_ids", sent_metadata)
        self.assertEqual(sent_metadata["source_atom_ids"], list(ingested.atom_ids))
        links = repository.list_atom_links(
            namespace="project-a", relation=AtomLinkRelation.DERIVED_FROM
        )
        self.assertEqual(len(links), len(ingested.atom_ids))

    def test_mem0_empty_result_is_retryable_by_default(self) -> None:
        repository = InMemoryRepository()
        IngestService(repository).ingest_text(
            namespace="project-a", source="empty-memory", text="Hello there."
        )
        processor = _FakeMem0Processor(())
        service = Mem0BootstrapService(repository, processor)

        first = service.run(namespace="project-a")
        second = service.run(namespace="project-a")

        self.assertEqual(first.memories_returned, 0)
        self.assertEqual(first.empty_batches, 1)
        self.assertEqual(first.batches_processed, 1)
        self.assertEqual(second.batches_resumed, 0)
        self.assertEqual(len(processor.calls), 2)

    def test_mem0_empty_result_can_be_explicitly_accepted(self) -> None:
        repository = InMemoryRepository()
        IngestService(repository).ingest_text(
            namespace="project-a", source="empty-memory", text="Hello there."
        )
        processor = _FakeMem0Processor(())
        service = Mem0BootstrapService(repository, processor, accept_empty=True)

        service.run(namespace="project-a")
        resumed = service.run(namespace="project-a")

        self.assertEqual(resumed.batches_resumed, 1)
        self.assertEqual(len(processor.calls), 1)

    def test_mem0_profile_change_reprocesses_completed_batch(self) -> None:
        repository = InMemoryRepository()
        IngestService(repository).ingest_text(
            namespace="project-a", source="profiled", text="The release is Friday."
        )
        first_processor = _FakeMem0Processor(
            ({"id": "memory-1", "memory": "The release is Friday."},),
            profile_id="test-profile-v1",
        )
        second_processor = _FakeMem0Processor(
            ({"id": "memory-2", "memory": "The release is scheduled for Friday."},),
            profile_id="test-profile-v2",
        )

        Mem0BootstrapService(repository, first_processor).run(namespace="project-a")
        rerun = Mem0BootstrapService(repository, second_processor).run(
            namespace="project-a"
        )

        self.assertEqual(rerun.batches_resumed, 0)
        self.assertEqual(rerun.batches_processed, 1)
        self.assertEqual(len(second_processor.calls), 1)
        self.assertEqual(
            second_processor.calls[0]["metadata"]["processor_profile"],
            "test-profile-v2",
        )

    def test_source_adjacency_can_recover_neighboring_context(self) -> None:
        repository = InMemoryRepository()
        result = IngestService(
            repository, chunker=TextChunker(max_chars=100, overlap_chars=0)
        ).ingest_text(
            namespace="project-a",
            source="two-parts",
            text=(
                "The launch codeword is heliotrope and identifies the deployment.\n\n"
                "The second paragraph contains the operational checklist and rollback steps."
            ),
        )
        self.assertEqual(len(result.atom_ids), 2)

        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(query="heliotrope", namespace="project-a")
        )

        neighbor = next(item for item in retrieval.items if item.atom_id == result.atom_ids[1])
        self.assertGreater(neighbor.score.relationship, 0.0)
        self.assertIn(f"adjacent_to={result.atom_ids[0]}", neighbor.score.evidence)

    def test_ingestion_initializes_relationships_and_is_replay_safe(self) -> None:
        repository = InMemoryRepository()
        result = IngestService(repository).ingest_text(
            namespace="project-a",
            source="architecture.txt",
            text="PostgreSQL powers the Data Retrieval architecture.",
            explicit_tags=("database", "data retrieval"),
        )
        relations = repository.list_tag_relations(
            namespace="project-a", relation_type="co_occurs"
        )
        self.assertEqual(len(relations), 1)
        original_weight = relations[0].weight_raw

        replay = TeacherCalibrationService(repository).calibrate_document(result.document_id)

        self.assertTrue(replay.idempotent)
        self.assertEqual(
            repository.list_tag_relations(
                namespace="project-a", relation_type="co_occurs"
            )[0].weight_raw,
            original_weight,
        )

    def test_mem0_import_is_native_boosted_and_idempotent(self) -> None:
        repository = InMemoryRepository()
        service = Mem0ImportService(repository)
        record = Mem0Record(
            record_id="memory-1",
            content="The Mac Mini runs remote inference for the retrieval worker.",
            tags=("mac mini", "remote inference"),
            metadata={"user_id": "user-1"},
        )

        first = service.import_records(namespace="project-a", records=(record,))
        atom = repository.get_atom(first.record_atom_ids[record.record_id][0])
        self.assertIsNotNone(atom)
        assert atom is not None
        self.assertEqual(atom.metadata["source_system"], "mem0")
        self.assertGreater(first.calibration_signals_created, 0)
        relation = repository.list_tag_relations(
            namespace="project-a", relation_type="co_occurs"
        )[0]
        first_weight = relation.weight_raw

        second = service.import_records(namespace="project-a", records=(record,))

        self.assertEqual(second.exact_duplicate_record_ids, ("memory-1",))
        self.assertEqual(second.calibration_signals_created, 0)
        self.assertEqual(
            repository.list_tag_relations(
                namespace="project-a", relation_type="co_occurs"
            )[0].weight_raw,
            first_weight,
        )

    def test_mem0_conflicts_are_explicit_uncertainty_links(self) -> None:
        repository = InMemoryRepository()
        records = (
            Mem0Record(
                record_id="old",
                content="The production database is SQLite.",
                tags=("database",),
                conflicts_with=("new",),
            ),
            Mem0Record(
                record_id="new",
                content="The production database is PostgreSQL.",
                tags=("database",),
            ),
        )

        result = Mem0ImportService(repository).import_records(
            namespace="project-a", records=records
        )

        self.assertEqual(result.conflict_links_created, 1)
        links = repository.list_atom_links(
            namespace="project-a", relation=AtomLinkRelation.CONFLICTS_WITH
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].confidence, 0.5)
        self.assertTrue(links[0].metadata["uncertainty"])

    def test_feedback_automatically_uses_mem0_multiplier(self) -> None:
        repository = InMemoryRepository()
        imported = Mem0ImportService(repository).import_records(
            namespace="project-a",
            records=(
                Mem0Record(
                    record_id="memory-1",
                    content="Docker runs the ingestion worker.",
                    tags=("docker",),
                ),
            ),
        )
        atom_id = imported.record_atom_ids["memory-1"][0]
        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(query="docker", namespace="project-a", query_tags=("docker",))
        )
        before = repository.atom_tags_for(atom_id)[0].weight_raw

        learned = LearningService(repository).apply_feedback(
            FeedbackRequest(
                feedback_id="mem0-feedback",
                retrieval_id=retrieval.retrieval_id,
                selected_atom_ids=(atom_id,),
                outcome="positive",
            )
        )

        self.assertEqual(learned.learning_multiplier, 2.0)
        self.assertAlmostEqual(repository.atom_tags_for(atom_id)[0].weight_raw, before + 0.1)

    def test_sqlite_persists_calibration_idempotency(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "calibration.sqlite3"
            with SQLiteRepository(path) as repository:
                result = IngestService(repository).ingest_text(
                    namespace="project-a",
                    source="source",
                    text="A PostgreSQL architecture note.",
                    explicit_tags=("database", "postgresql database"),
                )
            with SQLiteRepository(path) as repository:
                replay = TeacherCalibrationService(repository).calibrate_document(
                    result.document_id
                )
                self.assertTrue(replay.idempotent)

    def test_loads_common_mem0_export_shape(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "mem0.json"
            path.write_text(
                json.dumps(
                    {
                        "memories": [
                            {
                                "id": "m1",
                                "memory": "Use PostgreSQL.",
                                "metadata": {"tags": ["database"]},
                                "created_at": "2026-08-20T10:00:00+00:00",
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

            records = load_mem0_records(path)

        self.assertEqual(records[0].record_id, "m1")
        self.assertEqual(records[0].tags, ("database",))
        self.assertIsNotNone(records[0].occurred_at)


if __name__ == "__main__":
    unittest.main()

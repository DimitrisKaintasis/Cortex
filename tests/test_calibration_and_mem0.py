from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from data_retrieval.calibration import TeacherCalibrationService
from data_retrieval.domain.models import AtomLinkRelation, AtomRole
from data_retrieval.ingestion.chunker import TextChunker
from data_retrieval.mem0 import (
    Mem0BootstrapService,
    Mem0Entity,
    Mem0EntityRelationship,
    Mem0ImportService,
    Mem0ProcessResult,
    Mem0Record,
    Mem0VectorCalibrationService,
    load_mem0_records,
    normalize_mem0_response,
)
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan, RetrievalChannels
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.sqlite import SQLiteRepository


class _FakeMem0Processor:
    def __init__(
        self,
        result: Mem0ProcessResult,
        *,
        profile_id: str = "test-profile-v1",
    ) -> None:
        self.result = result
        self.profile_id = profile_id
        self.calls: list[dict[str, object]] = []

    def add(
        self,
        messages: tuple[dict[str, str], ...],
        *,
        user_id: str,
        run_id: str | None,
        metadata: dict[str, object],
    ) -> Mem0ProcessResult:
        self.calls.append(
            {
                "messages": messages,
                "user_id": user_id,
                "run_id": run_id,
                "metadata": metadata,
            }
        )
        return self.result


class _AlignedEmbedder:
    provider = "fixture"
    model = "aligned-v1"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((1.0, 0.0) for _ in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return (1.0, 0.0)


class CalibrationAndMem0Tests(unittest.TestCase):
    def test_mem0_response_requires_explicit_endpoint_provenance(self) -> None:
        response = {
            "results": [{"id": "memory-1", "memory": "Diagnostic only."}],
            "relations": {
                "provenance_relationships": [
                    {
                        "source": "alice",
                        "relationship": "works_at",
                        "destination": "openai",
                        "evidence_source_ids": ["atom-1"],
                        "provenance_valid": True,
                    }
                ]
            },
        }
        result = normalize_mem0_response(
            response,
            messages=({"role": "user", "content": "Alice works at OpenAI."},),
            source_atom_ids=("atom-1",),
            batch_id="batch-1",
        )
        self.assertEqual(len(result.memories), 1)
        self.assertEqual(len(result.entities), 2)
        self.assertEqual(len(result.relationships), 1)
        self.assertEqual(result.relationships[0].support_atom_ids, ("atom-1",))

    def test_mem0_response_quarantines_invalid_provenance(self) -> None:
        response = {
            "results": [],
            "relations": {
                "provenance_relationships": [
                    {
                        "source": "alice",
                        "relationship": "works_at",
                        "destination": "openai",
                        "evidence_source_ids": ["invented"],
                        "provenance_valid": False,
                    }
                ]
            },
        }
        result = normalize_mem0_response(
            response,
            messages=({"role": "user", "content": "Alice works at OpenAI."},),
            source_atom_ids=("atom-1",),
            batch_id="batch-1",
        )
        self.assertEqual(result.entities, ())
        self.assertEqual(result.relationships, ())
        self.assertEqual(result.relationships_quarantined, 1)
        self.assertIn("mem0_relationship_invalid_provenance", result.warnings)

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
        processor = _FakeMem0Processor(Mem0ProcessResult())
        Mem0BootstrapService(repository, processor).run(namespace="project-a", max_documents=1)
        sent_messages = processor.calls[0]["messages"]
        self.assertEqual(sent_messages[0]["content"], "Old state")
        self.assertIsNone(processor.calls[0]["run_id"])

    def test_entity_graph_bootstrap_is_private_tagless_and_replay_safe(self) -> None:
        repository = InMemoryRepository()
        ingested = IngestService(
            repository, chunker=TextChunker(max_chars=100, overlap_chars=0)
        ).ingest_text(
            namespace="project-a",
            source="conversation",
            text=(
                "Alice manages PostgreSQL storage for the project.\n\n"
                "OpenAI provides an inference service used by Alice."
            ),
            explicit_tags=("architecture",),
            metadata={"expected_answer": "must-not-leak"},
        )
        entities = (
            Mem0Entity("alice", "Alice", ingested.atom_ids, entity_type="person"),
            Mem0Entity("openai", "OpenAI", (ingested.atom_ids[1],), entity_type="org"),
        )
        relationship = Mem0EntityRelationship(
            "alice-openai",
            "alice",
            "openai",
            "uses_service_from",
            (ingested.atom_ids[1],),
            confidence=0.9,
        )
        processor = _FakeMem0Processor(
            Mem0ProcessResult(
                memories=({"id": "diagnostic-fact"},),
                entities=entities,
                relationships=(relationship,),
            )
        )
        service = Mem0BootstrapService(repository, processor, atom_batch_size=10)
        first = service.run(namespace="project-a")
        replay = service.run(namespace="project-a")
        self.assertEqual(first.mem0_records_returned, 1)
        self.assertEqual(first.entities_imported, 2)
        self.assertEqual(first.entity_support_links_created, 3)
        self.assertEqual(first.entity_relationship_links_created, 1)
        self.assertEqual(replay.batches_resumed, 1)
        self.assertEqual(len(processor.calls), 1)
        self.assertNotIn("expected_answer", processor.calls[0]["metadata"])
        derived = repository.list_atoms(namespace="project-a", role=AtomRole.DERIVED)
        self.assertEqual({atom.content for atom in derived}, {"Alice", "OpenAI"})
        self.assertTrue(all(repository.atom_tags_for(atom.atom_id) == () for atom in derived))
        relations = repository.list_atom_links(
            namespace="project-a", relation=AtomLinkRelation.MEM0_ENTITY_RELATION
        )
        self.assertEqual(len(relations), 1)
        self.assertEqual(relations[0].metadata["predicates"], ["uses_service_from"])

    def test_mem0_empty_result_is_retryable_unless_accepted(self) -> None:
        repository = InMemoryRepository()
        IngestService(repository).ingest_text(
            namespace="project-a", source="empty", text="Hello there."
        )
        processor = _FakeMem0Processor(Mem0ProcessResult())
        service = Mem0BootstrapService(repository, processor)
        first = service.run(namespace="project-a")
        second = service.run(namespace="project-a")
        self.assertEqual(first.empty_batches, 1)
        self.assertEqual(second.batches_resumed, 0)
        self.assertEqual(len(processor.calls), 2)

        accepted_repository = InMemoryRepository()
        IngestService(accepted_repository).ingest_text(
            namespace="project-a", source="empty", text="Hello there."
        )
        accepted_processor = _FakeMem0Processor(Mem0ProcessResult())
        accepted_service = Mem0BootstrapService(
            accepted_repository, accepted_processor, accept_empty=True
        )
        accepted_service.run(namespace="project-a")
        accepted_replay = accepted_service.run(namespace="project-a")
        self.assertEqual(accepted_replay.batches_resumed, 1)
        self.assertEqual(len(accepted_processor.calls), 1)

    def test_mem0_profile_change_reprocesses_completed_batch(self) -> None:
        repository = InMemoryRepository()
        IngestService(repository).ingest_text(
            namespace="project-a", source="profiled", text="The release is Friday."
        )
        first = _FakeMem0Processor(Mem0ProcessResult(), profile_id="test-profile-v1")
        second = _FakeMem0Processor(Mem0ProcessResult(), profile_id="test-profile-v2")
        Mem0BootstrapService(repository, first, accept_empty=True).run(namespace="project-a")
        rerun = Mem0BootstrapService(repository, second, accept_empty=True).run(
            namespace="project-a"
        )
        self.assertEqual(rerun.batches_resumed, 0)
        self.assertEqual(rerun.batches_processed, 1)
        self.assertEqual(second.calls[0]["metadata"]["processor_profile"], "test-profile-v2")

    def test_mem0_entity_path_recovers_related_source_evidence(self) -> None:
        repository = InMemoryRepository()
        first = IngestService(repository).ingest_text(
            namespace="project-a",
            source="alice-note",
            text="Alice approved the durable storage design.",
        )
        second = IngestService(repository).ingest_text(
            namespace="project-a",
            source="service-note",
            text="The inference service runs remotely overnight.",
        )
        processor = _FakeMem0Processor(
            Mem0ProcessResult(
                entities=(
                    Mem0Entity("alice", "Alice", first.atom_ids),
                    Mem0Entity("service", "Inference Service", second.atom_ids),
                ),
                relationships=(
                    Mem0EntityRelationship(
                        "uses",
                        "alice",
                        "service",
                        "uses",
                        (*first.atom_ids, *second.atom_ids),
                    ),
                ),
            )
        )
        Mem0BootstrapService(repository, processor).run(namespace="project-a")
        before = RetrievalService(
            repository,
            channels=RetrievalChannels(
                tags=False,
                semantic=False,
                temporal=False,
                temporal_summaries=False,
            ),
        ).retrieve(QueryPlan(query="Alice", namespace="project-a"))
        self.assertFalse(
            any(
                item.atom_id == second.atom_ids[0] and item.score.relationship > 0.0
                for item in before.items
            )
        )
        Mem0VectorCalibrationService(repository, _AlignedEmbedder()).calibrate_namespace(
            "project-a"
        )
        result = RetrievalService(
            repository,
            channels=RetrievalChannels(
                tags=False,
                semantic=False,
                temporal=False,
                temporal_summaries=False,
            ),
        ).retrieve(QueryPlan(query="Alice", namespace="project-a"))
        related = next(item for item in result.items if item.atom_id == second.atom_ids[0])
        self.assertGreater(related.score.relationship, 0.0)
        self.assertTrue(
            any(value.startswith("mem0_entity_path=") for value in related.score.evidence)
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
        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(query="heliotrope", namespace="project-a")
        )
        neighbor = next(item for item in retrieval.items if item.atom_id == result.atom_ids[1])
        self.assertGreater(neighbor.score.relationship, 0.0)

    def test_ingestion_initializes_relationships_and_is_replay_safe(self) -> None:
        repository = InMemoryRepository()
        result = IngestService(repository).ingest_text(
            namespace="project-a",
            source="architecture.txt",
            text="PostgreSQL powers the Data Retrieval architecture.",
            explicit_tags=("database", "data retrieval"),
        )
        relation = repository.list_tag_relations(namespace="project-a", relation_type="co_occurs")[
            0
        ]
        replay = TeacherCalibrationService(repository).calibrate_document(result.document_id)
        self.assertTrue(replay.idempotent)
        self.assertEqual(
            repository.list_tag_relations(namespace="project-a", relation_type="co_occurs")[
                0
            ].weight_raw,
            relation.weight_raw,
        )

    def test_legacy_mem0_record_import_has_no_hidden_all_pairs_calibration(self) -> None:
        repository = InMemoryRepository()
        record = Mem0Record(
            record_id="memory-1",
            content="The Mac Mini runs remote inference.",
            tags=("mac mini", "remote inference"),
        )
        service = Mem0ImportService(repository)
        first = service.import_records(namespace="project-a", records=(record,))
        relation_weight = repository.list_tag_relations(
            namespace="project-a", relation_type="co_occurs"
        )[0].weight_raw
        replay = service.import_records(namespace="project-a", records=(record,))
        self.assertGreater(first.calibration_signals_created, 0)
        self.assertEqual(replay.calibration_signals_created, 0)
        self.assertEqual(
            repository.list_tag_relations(namespace="project-a", relation_type="co_occurs")[
                0
            ].weight_raw,
            relation_weight,
        )

    def test_mem0_fact_support_rejects_non_source_atoms(self) -> None:
        repository = InMemoryRepository()
        derived = IngestService(repository).ingest_text(
            namespace="project-a",
            source="derived-input",
            text="A model-generated interpretation.",
            atom_role=AtomRole.DERIVED,
        )
        record = Mem0Record(
            record_id="invalid",
            content="A second interpretation.",
            support_atom_ids=derived.atom_ids,
        )
        with self.assertRaisesRegex(ValueError, "source-role atoms"):
            Mem0ImportService(repository).import_records(namespace="project-a", records=(record,))

    def test_mem0_exact_raw_duplicate_does_not_turn_source_into_derived_fact(self) -> None:
        repository = InMemoryRepository()
        duplicate = IngestService(repository).ingest_text(
            namespace="project-a", source="original", text="PostgreSQL stores data."
        )
        other = IngestService(repository).ingest_text(
            namespace="project-a", source="other", text="The Mac runs inference."
        )
        result = Mem0ImportService(repository).import_records(
            namespace="project-a",
            records=(
                Mem0Record(
                    record_id="duplicate",
                    content="PostgreSQL stores data.",
                    support_atom_ids=other.atom_ids,
                ),
            ),
        )
        self.assertEqual(result.record_atom_ids["duplicate"], duplicate.atom_ids)
        self.assertEqual(result.source_lineage_links_created, 0)

    def test_mem0_conflicts_are_explicit_uncertainty_links(self) -> None:
        repository = InMemoryRepository()
        result = Mem0ImportService(repository).import_records(
            namespace="project-a",
            records=(
                Mem0Record("old", "The database is SQLite.", conflicts_with=("new",)),
                Mem0Record("new", "The database is PostgreSQL."),
            ),
        )
        links = repository.list_atom_links(
            namespace="project-a", relation=AtomLinkRelation.CONFLICTS_WITH
        )
        self.assertEqual(result.conflict_links_created, 1)
        self.assertEqual(links[0].confidence, 0.5)

    def test_feedback_automatically_uses_mem0_multiplier(self) -> None:
        repository = InMemoryRepository()
        imported = Mem0ImportService(repository).import_records(
            namespace="project-a",
            records=(Mem0Record("memory-1", "Docker runs the worker.", tags=("docker",)),),
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
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calibration.sqlite3"
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
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "mem0.json"
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

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_retrieval.domain.models import AtomLinkRelation
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.sqlite import SQLiteRepository


class LearningServiceTests(unittest.TestCase):
    def test_positive_feedback_updates_only_learned_relationships(self) -> None:
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        first = ingestion.ingest_text(
            namespace="project-a",
            source="first",
            text="The remote runtime handles the ingestion worker.",
            explicit_tags=("docker", "mac mini"),
        )
        second = ingestion.ingest_text(
            namespace="project-a",
            source="second",
            text="The background worker remains available overnight.",
            explicit_tags=("docker", "mac mini"),
        )
        related_only = ingestion.ingest_text(
            namespace="project-a",
            source="third",
            text="A separate note with no overlapping query vocabulary.",
            explicit_tags=("mac mini",),
        )
        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(
                query="docker",
                namespace="project-a",
                query_tags=("docker",),
            )
        )
        selected = tuple(
            atom_id
            for atom_id in (first.atom_ids[0], second.atom_ids[0])
            if atom_id in {item.atom_id for item in retrieval.items}
        )

        result = LearningService(repository).apply_feedback(
            FeedbackRequest(
                feedback_id="feedback-1",
                retrieval_id=retrieval.retrieval_id,
                selected_atom_ids=selected,
                outcome="positive",
            )
        )

        self.assertEqual(result.atom_link_updates, 1)
        co_used = repository.list_atom_links(
            namespace="project-a", relation=AtomLinkRelation.CO_USED
        )
        self.assertEqual(len(co_used), 1)
        self.assertEqual(co_used[0].weight_raw, 0.1)
        self.assertEqual(co_used[0].metadata, {"learned": True})
        self.assertEqual(len(repository.list_tag_relations(namespace="project-a")), 1)

        expanded = RetrievalService(repository).retrieve(
            QueryPlan(
                query="docker",
                namespace="project-a",
                query_tags=("docker",),
            )
        )
        related_item = next(
            item for item in expanded.items if item.atom_id == related_only.atom_ids[0]
        )
        self.assertGreater(related_item.score.relationship, 0.0)
        self.assertTrue(
            any(value == "related_tag=mac mini" for value in related_item.score.evidence)
        )

        with self.assertRaisesRegex(ValueError, "feedback_id already exists"):
            LearningService(repository).apply_feedback(
                FeedbackRequest(
                    feedback_id="feedback-1",
                    retrieval_id=retrieval.retrieval_id,
                    selected_atom_ids=selected,
                    outcome="positive",
                )
            )

    def test_sqlite_persists_retrieval_and_feedback_updates(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            database = Path(temporary_directory) / "retrieval.sqlite3"
            with SQLiteRepository(database) as repository:
                ingested = IngestService(repository).ingest_text(
                    namespace="project-a",
                    source="sqlite",
                    text="SQLite stores retrieval audit events.",
                    explicit_tags=("sqlite",),
                )
                retrieval = RetrievalService(repository).retrieve(
                    QueryPlan(
                        query="sqlite",
                        namespace="project-a",
                        query_tags=("sqlite",),
                    )
                )
                result = LearningService(repository).apply_feedback(
                    FeedbackRequest(
                        feedback_id="feedback-sqlite",
                        retrieval_id=retrieval.retrieval_id,
                        selected_atom_ids=(ingested.atom_ids[0],),
                        outcome="positive",
                    )
                )
                edge = repository.atom_tags_for(ingested.atom_ids[0])[0]

                self.assertEqual(result.atom_tag_updates, 1)
                self.assertEqual(edge.weight_raw, 1.05)
                self.assertIn("feedback-sqlite", edge.evidence_sources)

            with SQLiteRepository(database) as reopened:
                event = reopened.get_retrieval_event(retrieval.retrieval_id)
                self.assertIsNotNone(event)
                self.assertEqual(event["namespace"], "project-a")
                persisted = reopened.atom_tags_for(ingested.atom_ids[0])[0]
                self.assertEqual(persisted.weight_raw, 1.05)


if __name__ == "__main__":
    unittest.main()

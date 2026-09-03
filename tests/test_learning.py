from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_retrieval.domain.models import AtomLinkRelation, CalibrationTarget
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import (
    ATOM_CO_USED_LEARNING_POLICY,
    ATOM_TAG_ONLY_LEARNING_POLICY,
    CO_USED_ONLY_LEARNING_POLICY,
    QUERY_EVIDENCE_LEARNING_POLICY,
    LearningService,
)
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.sqlite import SQLiteRepository


class LearningServiceTests(unittest.TestCase):
    def test_negative_feedback_reverses_selected_support_without_collateral_change(
        self,
    ) -> None:
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        first = ingestion.ingest_text(
            namespace="project-a",
            source="first",
            text="PostgreSQL stores the selected architecture.",
            explicit_tags=("architecture", "postgresql"),
        )
        second = ingestion.ingest_text(
            namespace="project-a",
            source="second",
            text="The retrieval service uses the selected architecture.",
            explicit_tags=("architecture", "retrieval"),
        )
        collateral = ingestion.ingest_text(
            namespace="project-a",
            source="collateral",
            text="The Mac Mini has a replaceable architecture.",
            explicit_tags=("architecture", "mac mini"),
        )
        selected_ids = (first.atom_ids[0], second.atom_ids[0])

        def architecture_weight(atom_id: str) -> float:
            tags = {tag.tag_id: tag.canonical_text for tag in repository.list_tags("project-a")}
            return next(
                edge.weight_raw
                for edge in repository.atom_tags_for(atom_id)
                if tags[edge.tag_id] == "architecture"
            )

        baseline_selected = tuple(architecture_weight(atom_id) for atom_id in selected_ids)
        baseline_collateral = architecture_weight(collateral.atom_ids[0])
        baseline_relations = {
            (edge.source_tag_id, edge.target_tag_id): edge.weight_raw
            for edge in repository.list_tag_relations(namespace="project-a")
        }
        positive_retrieval = RetrievalService(repository).retrieve(
            QueryPlan(
                query="Which architecture is used?",
                namespace="project-a",
                query_tags=("architecture",),
            )
        )
        service = LearningService(repository, policy=QUERY_EVIDENCE_LEARNING_POLICY)
        service.apply_feedback(
            FeedbackRequest(
                feedback_id="positive-feedback",
                retrieval_id=positive_retrieval.retrieval_id,
                selected_atom_ids=selected_ids,
                outcome="positive",
            )
        )
        negative_retrieval = RetrievalService(repository).retrieve(
            QueryPlan(
                query="Which architecture is used?",
                namespace="project-a",
                query_tags=("architecture",),
            )
        )
        negative = service.apply_feedback(
            FeedbackRequest(
                feedback_id="negative-feedback",
                retrieval_id=negative_retrieval.retrieval_id,
                selected_atom_ids=selected_ids,
                outcome="negative",
            )
        )

        self.assertEqual(negative.atom_tag_updates, 2)
        self.assertEqual(negative.atom_link_updates, 1)
        self.assertEqual(negative.tag_relation_updates, 2)
        self.assertEqual(
            tuple(architecture_weight(atom_id) for atom_id in selected_ids),
            baseline_selected,
        )
        self.assertEqual(architecture_weight(collateral.atom_ids[0]), baseline_collateral)
        final_relations = {
            (edge.source_tag_id, edge.target_tag_id): edge.weight_raw
            for edge in repository.list_tag_relations(namespace="project-a")
        }
        self.assertEqual(set(final_relations), set(baseline_relations))
        for key, baseline_weight in baseline_relations.items():
            self.assertAlmostEqual(final_relations[key], baseline_weight)
        co_used = repository.list_atom_links(
            namespace="project-a",
            relation=AtomLinkRelation.CO_USED,
        )
        self.assertEqual(len(co_used), 1)
        self.assertEqual(co_used[0].weight_raw, 0.0)
        self.assertTrue(WeightLedgerService(repository).audit_namespace("project-a").passed)

    def test_query_evidence_policy_omits_unattributed_tag_pairs(self) -> None:
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        first = ingestion.ingest_text(
            namespace="project-a",
            source="first",
            text="PostgreSQL stores the retrieval architecture.",
            explicit_tags=("architecture", "postgresql", "retrieval"),
        )
        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(
                query="Which architecture is used?",
                namespace="project-a",
                query_tags=("architecture",),
            )
        )

        result = LearningService(
            repository,
            policy=QUERY_EVIDENCE_LEARNING_POLICY,
        ).apply_feedback(
            FeedbackRequest(
                feedback_id="query-evidence-feedback",
                retrieval_id=retrieval.retrieval_id,
                selected_atom_ids=(first.atom_ids[0],),
                outcome="positive",
            )
        )

        self.assertEqual(result.tag_relation_updates, 2)
        learned_relations = tuple(
            event
            for event in repository.list_weight_events(namespace="project-a")
            if event.source_id == "query-evidence-feedback"
            and event.target_type is CalibrationTarget.TAG_RELATION
        )
        self.assertEqual(len(learned_relations), 2)
        tags = {tag.tag_id: tag.canonical_text for tag in repository.list_tags("project-a")}
        learned_pairs = {
            frozenset((tags[event.target_id], tags[event.related_id]))
            for event in learned_relations
        }
        self.assertEqual(
            learned_pairs,
            {
                frozenset(("architecture", "postgresql")),
                frozenset(("architecture", "retrieval")),
            },
        )

    def test_single_channel_policies_isolate_atom_tags_and_co_used(self) -> None:
        for policy, expected_counts in (
            (ATOM_TAG_ONLY_LEARNING_POLICY, (2, 0, 0)),
            (CO_USED_ONLY_LEARNING_POLICY, (0, 1, 0)),
        ):
            with self.subTest(policy=policy.policy_id):
                repository = InMemoryRepository()
                ingestion = IngestService(repository)
                first = ingestion.ingest_text(
                    namespace="project-a",
                    source="first",
                    text="The Mac runs the Docker worker.",
                    explicit_tags=("docker", "mac mini"),
                )
                second = ingestion.ingest_text(
                    namespace="project-a",
                    source="second",
                    text="The Mac keeps the Docker worker online.",
                    explicit_tags=("docker", "mac mini"),
                )
                retrieval = RetrievalService(repository).retrieve(
                    QueryPlan(
                        query="docker",
                        namespace="project-a",
                        query_tags=("docker",),
                    )
                )

                result = LearningService(repository, policy=policy).apply_feedback(
                    FeedbackRequest(
                        feedback_id=f"feedback-{policy.policy_id}",
                        retrieval_id=retrieval.retrieval_id,
                        selected_atom_ids=(first.atom_ids[0], second.atom_ids[0]),
                        outcome="positive",
                    )
                )

                self.assertEqual(
                    (
                        result.atom_tag_updates,
                        result.atom_link_updates,
                        result.tag_relation_updates,
                    ),
                    expected_counts,
                )

    def test_atom_co_used_policy_avoids_broad_tag_relation_updates(self) -> None:
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        first = ingestion.ingest_text(
            namespace="project-a",
            source="first",
            text="The Mac runs the Docker worker.",
            explicit_tags=("docker", "mac mini"),
        )
        second = ingestion.ingest_text(
            namespace="project-a",
            source="second",
            text="The Mac keeps the Docker worker online.",
            explicit_tags=("docker", "mac mini"),
        )
        tag_relations_before = repository.list_tag_relations(namespace="project-a")
        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(query="docker", namespace="project-a", query_tags=("docker",))
        )

        result = LearningService(
            repository,
            policy=ATOM_CO_USED_LEARNING_POLICY,
        ).apply_feedback(
            FeedbackRequest(
                feedback_id="focused-feedback-1",
                retrieval_id=retrieval.retrieval_id,
                selected_atom_ids=(first.atom_ids[0], second.atom_ids[0]),
                outcome="positive",
            )
        )

        self.assertEqual(result.atom_tag_updates, 2)
        self.assertEqual(result.atom_link_updates, 1)
        self.assertEqual(result.tag_relation_updates, 0)
        self.assertEqual(
            repository.list_tag_relations(namespace="project-a"),
            tag_relations_before,
        )
        learned_events = tuple(
            event
            for event in repository.list_weight_events(namespace="project-a")
            if event.source_id == "focused-feedback-1"
        )
        self.assertEqual(len(learned_events), 3)
        self.assertNotIn(
            CalibrationTarget.TAG_RELATION,
            {event.target_type for event in learned_events},
        )
        self.assertEqual(
            {event.policy_version for event in learned_events},
            {ATOM_CO_USED_LEARNING_POLICY.policy_id},
        )

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

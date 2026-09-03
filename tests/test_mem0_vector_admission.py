from __future__ import annotations

import unittest
from dataclasses import replace

from data_retrieval.domain.models import AtomLinkRelation
from data_retrieval.mem0 import (
    Mem0AdmissionDisposition,
    Mem0Entity,
    Mem0EntityImportService,
    Mem0EntityRelationship,
    Mem0VectorCalibrationService,
)
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.memory import InMemoryRepository


class _FixtureEmbedder:
    provider = "fixture"
    model = "corroboration-v1"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        if text == "Mira and Acme Labs appear in an unrelated list.":
            return (0.0, 1.0)
        return (1.0, 0.0)


class Mem0VectorAdmissionTests(unittest.TestCase):
    def test_proposals_start_inactive_then_receive_bounded_decisions(self) -> None:
        repository = InMemoryRepository()
        high = IngestService(repository).ingest_text(
            namespace="project-a",
            source="high",
            text="Alice leads Project Helios.",
        )
        low = IngestService(repository).ingest_text(
            namespace="project-a",
            source="low",
            text="Mira and Acme Labs appear in an unrelated list.",
        )
        pronoun = IngestService(repository).ingest_text(
            namespace="project-a",
            source="pronoun",
            text="He deployed Atlas.",
        )
        entities = (
            Mem0Entity("alice", "Alice", high.atom_ids),
            Mem0Entity("helios", "Project Helios", high.atom_ids),
            Mem0Entity("mira", "Mira", low.atom_ids),
            Mem0Entity("acme", "Acme Labs", low.atom_ids),
            Mem0Entity("leo", "Leo", pronoun.atom_ids),
            Mem0Entity("atlas", "Atlas", pronoun.atom_ids),
        )
        relationships = (
            Mem0EntityRelationship(
                "high", "alice", "helios", "leads", high.atom_ids
            ),
            Mem0EntityRelationship(
                "low", "mira", "acme", "founded", low.atom_ids
            ),
            Mem0EntityRelationship(
                "pronoun", "leo", "atlas", "deployed", pronoun.atom_ids
            ),
        )
        Mem0EntityImportService(repository).import_graph(
            namespace="project-a",
            batch_id="batch-1",
            entities=entities,
            relationships=relationships,
        )
        initial = repository.list_atom_links(
            namespace="project-a",
            relation=AtomLinkRelation.MEM0_ENTITY_RELATION,
        )
        self.assertEqual({link.weight_raw for link in initial}, {0.0})
        self.assertEqual(
            {link.metadata["admission_state"] for link in initial}, {"unreviewed"}
        )

        service = Mem0VectorCalibrationService(repository, _FixtureEmbedder())
        first = service.calibrate_namespace("project-a", max_links=1)
        second = service.calibrate_namespace("project-a", max_links=1)
        third = service.calibrate_namespace("project-a", max_links=1)
        replay = service.calibrate_namespace("project-a", max_links=1)

        decisions = (*first.decisions, *second.decisions, *third.decisions)
        by_disposition = {decision.disposition: decision for decision in decisions}
        self.assertEqual(
            sum(result.provisional_links for result in (first, second, third)), 1
        )
        self.assertEqual(sum(result.held_links for result in (first, second, third)), 1)
        self.assertEqual(
            sum(result.rejected_links for result in (first, second, third)), 1
        )
        self.assertEqual(
            by_disposition[Mem0AdmissionDisposition.PROVISIONAL].initial_weight,
            0.25,
        )
        self.assertEqual(
            by_disposition[Mem0AdmissionDisposition.HOLD_FOR_REVIEW].reason_codes,
            ("endpoint_not_explicit_in_evidence",),
        )
        self.assertEqual(
            by_disposition[Mem0AdmissionDisposition.REJECTED].reason_codes,
            ("low_vector_corroboration",),
        )
        self.assertTrue(first.scope_truncated)
        self.assertTrue(second.scope_truncated)
        self.assertFalse(third.scope_truncated)
        self.assertEqual(
            (first.replayed_links, second.replayed_links, third.replayed_links),
            (0, 1, 2),
        )
        self.assertEqual(replay.links_calibrated, 0)
        self.assertEqual(replay.replayed_links, 3)
        self.assertFalse(replay.scope_truncated)

    def test_invalid_policy_order_is_rejected(self) -> None:
        from data_retrieval.mem0 import Mem0VectorAdmissionPolicy

        with self.assertRaisesRegex(ValueError, "must exceed"):
            Mem0VectorAdmissionPolicy(
                reject_below_similarity=0.8,
                provisional_above_similarity=0.8,
            )

    def test_new_import_profile_resets_a_legacy_active_link(self) -> None:
        repository = InMemoryRepository()
        source = IngestService(repository).ingest_text(
            namespace="project-a",
            source="source",
            text="Alice leads Project Helios.",
        )
        entities = (
            Mem0Entity("alice", "Alice", source.atom_ids),
            Mem0Entity("helios", "Project Helios", source.atom_ids),
        )
        importer = Mem0EntityImportService(repository)
        importer.import_graph(
            namespace="project-a",
            batch_id="batch-1",
            entities=entities,
            relationships=(
                Mem0EntityRelationship(
                    "legacy-proposal", "alice", "helios", "leads", source.atom_ids
                ),
            ),
        )
        candidate = repository.list_atom_links(
            namespace="project-a",
            relation=AtomLinkRelation.MEM0_ENTITY_RELATION,
        )[0]
        legacy_metadata = {
            key: value
            for key, value in candidate.metadata.items()
            if key not in {"proposal_weight_raw", "admission_state"}
        }
        repository.restore_weight_aggregates(
            atom_tags=(),
            atom_links=(replace(candidate, weight_raw=1.0, metadata=legacy_metadata),),
            tag_relations=(),
        )

        importer.import_graph(
            namespace="project-a",
            batch_id="batch-1",
            entities=entities,
            relationships=(
                Mem0EntityRelationship(
                    "v2-proposal", "alice", "helios", "leads", source.atom_ids
                ),
            ),
        )

        migrated = repository.list_atom_links(
            namespace="project-a",
            relation=AtomLinkRelation.MEM0_ENTITY_RELATION,
        )[0]
        self.assertEqual(migrated.weight_raw, 0.0)
        self.assertEqual(migrated.metadata["admission_state"], "unreviewed")


if __name__ == "__main__":
    unittest.main()

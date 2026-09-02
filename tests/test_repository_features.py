from __future__ import annotations

import unittest
from dataclasses import replace
from datetime import UTC, datetime

from data_retrieval.collective import (
    RepositoryFeatureCandidate,
    ShadowRepositoryEvidenceAdapter,
)
from data_retrieval.core.identifiers import content_hash
from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    AtomRole,
    Document,
    IngestionBundle,
)
from data_retrieval.retrieval.models import AtomEmbedding
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.memory import InMemoryRepository


class ShadowRepositoryEvidenceAdapterTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryRepository()
        ingestion = IngestService(self.repository)
        current = ingestion.ingest_text(
            namespace="project-a",
            source="current",
            text="PostgreSQL is the selected durable graph store.",
            explicit_tags=("storage architecture",),
            occurred_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        old = ingestion.ingest_text(
            namespace="project-a",
            source="old",
            text="SQLite was the earlier durable graph store.",
            explicit_tags=("storage architecture",),
            occurred_at=datetime(2026, 7, 1, tzinfo=UTC),
        )
        target = ingestion.ingest_text(
            namespace="project-a",
            source="target",
            text="PostgreSQL supports relational persistence and vector search.",
            explicit_tags=("postgresql",),
            occurred_at=datetime(2026, 8, 2, tzinfo=UTC),
        )
        ingestion.ingest_text(
            namespace="project-a",
            source="relationship",
            text="The architecture uses PostgreSQL for durable storage.",
            explicit_tags=("storage architecture", "postgresql"),
        )
        tags = {
            tag.canonical_text: tag for tag in self.repository.list_tags("project-a")
        }
        relation = self.repository.list_tag_relations(namespace="project-a")[0]
        self.repository.apply_learning_updates(
            feedback_event={
                "feedback_id": "feedback-1",
                "retrieval_id": "retrieval-1",
                "namespace": "project-a",
                "outcome": "positive",
            },
            atom_tags=(),
            atom_links=(),
            tag_relations=(replace(relation, weight_raw=relation.weight_raw + 0.2),),
        )
        mem0_document = Document(
            document_id="mem0-doc",
            namespace="project-a",
            source="mem0:fixture",
            content_hash=content_hash("durable PostgreSQL storage"),
        )
        mem0_atom = Atom(
            atom_id="mem0-atom",
            document_id=mem0_document.document_id,
            namespace="project-a",
            position=0,
            char_start=0,
            char_end=27,
            content="durable PostgreSQL storage",
            content_hash=content_hash("durable PostgreSQL storage"),
            kind=AtomKind.SOURCE,
            role=AtomRole.DERIVED,
            metadata={"source_system": "mem0"},
        )
        self.repository.persist_ingestion(
            IngestionBundle(
                document=mem0_document,
                atoms=(mem0_atom,),
                tags=(),
                atom_tags=(),
                atom_links=(
                    AtomLink(
                        from_atom_id=mem0_atom.atom_id,
                        to_atom_id=current.atom_ids[0],
                        relation=AtomLinkRelation.SUPPORTED_BY,
                        metadata={"source_system": "mem0", "lineage": True},
                    ),
                    AtomLink(
                        from_atom_id=mem0_atom.atom_id,
                        to_atom_id=old.atom_ids[0],
                        relation=AtomLinkRelation.CONFLICTS_WITH,
                        metadata={"source_system": "mem0", "uncertainty": True},
                    ),
                    AtomLink(
                        from_atom_id=current.atom_ids[0],
                        to_atom_id=old.atom_ids[0],
                        relation=AtomLinkRelation.SUPERSEDES,
                    ),
                ),
            )
        )
        vectors = {
            current.atom_ids[0]: (1.0, 0.0, 0.0),
            old.atom_ids[0]: (0.3, 0.7, 0.0),
            target.atom_ids[0]: (0.95, 0.05, 0.0),
        }
        atoms = self.repository.get_atoms(tuple(vectors))
        self.repository.upsert_embeddings(
            tuple(
                AtomEmbedding(
                    atom_id=atom.atom_id,
                    provider="fixture",
                    model="fixture-v1",
                    dimensions=3,
                    vector=vectors[atom.atom_id],
                    content_hash=atom.content_hash,
                )
                for atom in atoms
            )
        )
        self.current_id = current.atom_ids[0]
        self.old_id = old.atom_ids[0]
        self.target_id = target.atom_ids[0]
        self.source_tag_id = tags["storage architecture"].tag_id
        self.target_tag_id = tags["postgresql"].tag_id

    def _candidate(self) -> RepositoryFeatureCandidate:
        return RepositoryFeatureCandidate(
            entry_id="fixture-relation",
            namespace="project-a",
            source_tag_id=self.source_tag_id,
            target_tag_id=self.target_tag_id,
            relation_type="co_occurs",
            source_atom_ids=(self.current_id, self.old_id),
            target_atom_ids=(self.target_id,),
            embedding_provider="fixture",
            embedding_model="fixture-v1",
        )

    @staticmethod
    def _components(result) -> dict[str, object]:
        return {item.name: item.value for item in result.features.components}

    def test_adapter_reads_ledger_vectors_mem0_and_temporal_without_writes(self) -> None:
        adapter = ShadowRepositoryEvidenceAdapter(self.repository)
        before = (
            len(self.repository.list_weight_events(namespace="project-a")),
            self.repository.atom_link_count,
            len(self.repository.list_tag_relations(namespace="project-a")),
        )

        result = adapter.evaluate(self._candidate())

        after = (
            len(self.repository.list_weight_events(namespace="project-a")),
            self.repository.atom_link_count,
            len(self.repository.list_tag_relations(namespace="project-a")),
        )
        components = self._components(result)
        self.assertEqual(before, after)
        self.assertEqual(
            set(result.features.evidence_sources),
            {"ledger", "vectors", "mem0", "temporal", "impact"},
        )
        self.assertFalse(components["contributor_independence_known"])
        self.assertEqual(components["relationship_maturity"], 0.0)
        self.assertEqual(components["mem0_unique_lineages"], 2)
        self.assertGreater(components["temporal_uncertainty"], 0.0)
        self.assertGreater(components["expected_impact"], 0.0)

    def test_namespace_report_is_payload_free_and_summarizes_missing_evidence(self) -> None:
        report = ShadowRepositoryEvidenceAdapter(self.repository).observe_namespace(
            namespace="project-a",
            embedding_provider="fixture",
            embedding_model="fixture-v1",
            limit=1,
        )

        payload = report.as_dict()
        self.assertEqual(report.candidate_count, 1)
        self.assertEqual(report.stored_relation_count, 1)
        self.assertEqual(report.eligible_relation_count, 1)
        self.assertEqual(report.skipped_missing_catalog_tags, 0)
        self.assertEqual(report.truncated_candidate_count, 0)
        self.assertNotIn("impact", report.missing_source_counts)
        self.assertIn("uncertainty", report.feature_distributions)
        self.assertTrue(report.observations[0].feature_components)
        self.assertNotIn("PostgreSQL supports", str(payload))
        self.assertNotIn(self.current_id, str(payload))

    def test_missing_cached_model_stays_missing_instead_of_embedding(self) -> None:
        candidate = replace(self._candidate(), embedding_model="not-cached")

        result = ShadowRepositoryEvidenceAdapter(self.repository).evaluate(candidate)

        self.assertIn("vectors", result.features.missing_sources)

    def test_same_atom_on_both_tags_is_not_a_perfect_vector_match(self) -> None:
        candidate = replace(
            self._candidate(),
            source_atom_ids=(self.current_id,),
            target_atom_ids=(self.current_id,),
        )

        result = ShadowRepositoryEvidenceAdapter(self.repository).evaluate(candidate)

        self.assertIn("vectors", result.features.missing_sources)


if __name__ == "__main__":
    unittest.main()

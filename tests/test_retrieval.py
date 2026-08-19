import unittest
from datetime import UTC, datetime, timedelta

from data_retrieval.domain.models import AtomLink, AtomLinkRelation, IngestionBundle
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.memory import InMemoryRepository


class StubEmbedder:
    provider = "stub"
    model = "semantic-v1"

    def __init__(self, vectors: dict[str, tuple[float, ...]]) -> None:
        self.vectors = vectors

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self.vectors[text] for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self.vectors[text]


class RetrievalServiceTests(unittest.TestCase):
    def test_retrieves_untimed_atom_by_explicit_tag_with_explanation(self) -> None:
        repository = InMemoryRepository()
        result = IngestService(repository).ingest_text(
            namespace="project-a",
            source="architecture",
            text="Raw atoms are persisted before optional enrichment.",
            explicit_tags=("architecture",),
        )

        retrieved = RetrievalService(repository).retrieve(
            QueryPlan(
                query="How does ingestion work?",
                namespace="project-a",
                query_tags=("architecture",),
            )
        )

        self.assertEqual(retrieved.items[0].atom_id, result.atom_ids[0])
        self.assertGreater(retrieved.items[0].score.tag, 0.0)
        self.assertIn("tag=architecture", retrieved.items[0].score.evidence)
        self.assertEqual(retrieved.resolved_temporal_mode, TemporalMode.NONE)

    def test_semantic_channel_finds_an_atom_without_shared_words(self) -> None:
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        target = ingestion.ingest_text(
            namespace="project-a",
            source="vehicles",
            text="A car engine converts fuel into motion.",
        )
        ingestion.ingest_text(
            namespace="project-a",
            source="cooking",
            text="Bread needs flour, water, yeast, and heat.",
        )
        embedder = StubEmbedder(
            {
                "A car engine converts fuel into motion.": (1.0, 0.0),
                "Bread needs flour, water, yeast, and heat.": (0.0, 1.0),
                "automobile powertrain": (1.0, 0.0),
            }
        )
        EmbeddingEnrichmentService(repository, embedder).enrich_namespace("project-a")

        retrieved = RetrievalService(repository, embedder=embedder).retrieve(
            QueryPlan(query="automobile powertrain", namespace="project-a")
        )

        self.assertEqual(retrieved.items[0].atom_id, target.atom_ids[0])
        self.assertEqual(retrieved.items[0].score.semantic, 1.0)

    def test_current_state_excludes_superseded_atom_but_as_of_keeps_it(self) -> None:
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        start = datetime(2026, 8, 1, 12, tzinfo=UTC)
        old = ingestion.ingest_text(
            namespace="project-a",
            source="mac/status/old",
            text="Docker is not installed on the Mac Mini.",
            explicit_tags=("docker",),
            occurred_at=start,
            metadata={"timeline_id": "main", "state_key": "mac-mini/docker"},
        )
        new = ingestion.ingest_text(
            namespace="project-a",
            source="mac/status/new",
            text="Docker is installed and working on the Mac Mini.",
            explicit_tags=("docker",),
            occurred_at=start + timedelta(days=2),
            metadata={"timeline_id": "main", "state_key": "mac-mini/docker"},
        )
        new_document = repository.get_document(new.document_id)
        new_atoms = repository.get_atoms_for_document(new.document_id)
        repository.persist_ingestion(
            IngestionBundle(
                document=new_document,
                atoms=new_atoms,
                tags=(),
                atom_tags=(),
                atom_links=(
                    AtomLink(
                        from_atom_id=new.atom_ids[0],
                        to_atom_id=old.atom_ids[0],
                        relation=AtomLinkRelation.SUPERSEDES,
                    ),
                ),
            )
        )
        service = RetrievalService(repository)

        current = service.retrieve(
            QueryPlan(
                query="current docker status",
                namespace="project-a",
                query_tags=("docker",),
                timeline_id="main",
            )
        )
        historical = service.retrieve(
            QueryPlan(
                query="docker status",
                namespace="project-a",
                query_tags=("docker",),
                timeline_id="main",
                temporal_mode=TemporalMode.AS_OF,
                as_of=start + timedelta(days=1),
            )
        )

        self.assertEqual(current.resolved_temporal_mode, TemporalMode.CURRENT_STATE)
        self.assertEqual(current.items[0].atom_id, new.atom_ids[0])
        self.assertNotIn(old.atom_ids[0], {item.atom_id for item in current.items})
        self.assertIn(old.atom_ids[0], {item.atom_id for item in historical.items})
        self.assertNotIn(new.atom_ids[0], {item.atom_id for item in historical.items})

    def test_range_mode_excludes_untimed_and_out_of_range_atoms(self) -> None:
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        start = datetime(2026, 8, 1, tzinfo=UTC)
        included = ingestion.ingest_text(
            namespace="project-a",
            source="included",
            text="Deployment completed in the selected range.",
            occurred_at=start + timedelta(hours=2),
        )
        ingestion.ingest_text(
            namespace="project-a",
            source="outside",
            text="Deployment was discussed outside the selected range.",
            occurred_at=start + timedelta(days=3),
        )
        ingestion.ingest_text(
            namespace="project-a",
            source="untimed",
            text="Deployment note without an event timestamp.",
        )

        retrieved = RetrievalService(repository).retrieve(
            QueryPlan(
                query="deployment",
                namespace="project-a",
                range_start=start,
                range_end=start + timedelta(days=1),
            )
        )

        self.assertEqual({item.atom_id for item in retrieved.items}, set(included.atom_ids))
        self.assertEqual(retrieved.resolved_temporal_mode, TemporalMode.RANGE)


if __name__ == "__main__":
    unittest.main()

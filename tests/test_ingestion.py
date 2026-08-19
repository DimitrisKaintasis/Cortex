import unittest

from data_retrieval.ingestion.chunker import TextChunker
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.memory import InMemoryRepository


class IngestServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryRepository()
        self.service = IngestService(
            repository=self.repository,
            chunker=TextChunker(max_chars=100, overlap_chars=15),
        )

    def test_ingestion_is_idempotent(self) -> None:
        text = "A sufficiently long document. " * 20
        first = self.service.ingest_text(
            namespace="project-a",
            source="notes/example.txt",
            text=text,
            explicit_tags=("Data Retrieval", "Architecture"),
        )
        second = self.service.ingest_text(
            namespace="project-a",
            source="notes/example.txt",
            text=text,
            explicit_tags=("Data Retrieval", "Architecture"),
        )

        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        self.assertEqual(first.document_id, second.document_id)
        self.assertEqual(first.atom_ids, second.atom_ids)
        self.assertEqual(self.repository.document_count, 1)
        self.assertEqual(self.repository.atom_count, len(first.atom_ids))
        self.assertEqual(self.repository.tag_count, 2)
        self.assertEqual(
            self.repository.atom_tag_count,
            self.repository.atom_count * self.repository.tag_count,
        )

    def test_tags_are_normalized_deduplicated_and_traceable(self) -> None:
        result = self.service.ingest_text(
            namespace="project-a",
            source="note.txt",
            text="A short but useful architecture note.",
            explicit_tags=(" API_Calls ", "api-calls", "Architecture!"),
        )

        tags = self.repository.list_tags("project-a")
        self.assertEqual([tag.canonical_text for tag in tags], ["api calls", "architecture"])
        self.assertEqual(len(result.tag_ids), 2)

        edges = self.repository.atom_tags_for(result.atom_ids[0])
        self.assertEqual(len(edges), 2)
        self.assertTrue(all(edge.evidence_sources == ("explicit",) for edge in edges))
        self.assertTrue(all(edge.weight_raw == 1.0 for edge in edges))

    def test_existing_catalog_tag_is_reused(self) -> None:
        first = self.service.ingest_text(
            namespace="project-a",
            source="first.txt",
            text="First document.",
            explicit_tags=("Architecture",),
        )
        second = self.service.ingest_text(
            namespace="project-a",
            source="second.txt",
            text="Second document.",
            explicit_tags=("architecture",),
        )

        self.assertEqual(first.tag_ids, second.tag_ids)
        self.assertEqual(self.repository.tag_count, 1)


if __name__ == "__main__":
    unittest.main()

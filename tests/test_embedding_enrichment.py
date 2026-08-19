import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from data_retrieval.retrieval.ollama import OllamaEmbedder
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.sqlite import SQLiteRepository


class FakeResponse:
    def __init__(self, body: dict[str, object]) -> None:
        self.body = json.dumps(body).encode("utf-8")

    def __enter__(self) -> "FakeResponse":
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self.body


class EmbeddingEnrichmentTests(unittest.TestCase):
    @patch("data_retrieval.tagging.ollama.urlopen")
    def test_ollama_embeddings_are_persisted_and_reused(self, mock_open) -> None:
        mock_open.return_value = FakeResponse({"embeddings": [[0.1, 0.2, 0.3]]})
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "data.sqlite3"
            with SQLiteRepository(path) as repository:
                ingested = IngestService(repository).ingest_text(
                    namespace="project-a",
                    source="notes",
                    text="Embedding enrichment is derived data.",
                )
                embedder = OllamaEmbedder(
                    base_url="http://127.0.0.1:11435",
                    model_name="embedding-test",
                )
                service = EmbeddingEnrichmentService(repository, embedder)

                first = service.enrich_namespace("project-a")
                second = service.enrich_namespace("project-a")
                stored = repository.get_embeddings(
                    atom_ids=ingested.atom_ids,
                    provider="ollama",
                    model="embedding-test",
                )

        self.assertEqual(first.embedded_atom_ids, ingested.atom_ids)
        self.assertEqual(second.reused_atom_ids, ingested.atom_ids)
        self.assertEqual(stored[ingested.atom_ids[0]].vector, (0.1, 0.2, 0.3))
        self.assertEqual(mock_open.call_count, 1)
        payload = json.loads(mock_open.call_args.args[0].data.decode("utf-8"))
        self.assertEqual(payload["input"], ["Embedding enrichment is derived data."])

    @patch("data_retrieval.tagging.ollama.urlopen")
    def test_harrier_profile_distinguishes_query_from_document_input(self, mock_open) -> None:
        mock_open.return_value = FakeResponse({"embeddings": [[0.4, 0.5]]})
        embedder = OllamaEmbedder(
            base_url="http://127.0.0.1:11435",
            model_name="harrier-test",
            profile_name="harrier-retrieval-v1",
        )

        embedder.embed_documents(("Stored evidence",))
        document_payload = json.loads(mock_open.call_args.args[0].data.decode("utf-8"))
        embedder.embed_query("Where is the evidence?")
        query_payload = json.loads(mock_open.call_args.args[0].data.decode("utf-8"))

        self.assertEqual(document_payload["input"], ["Stored evidence"])
        self.assertIn("Instruct:", query_payload["input"][0])
        self.assertTrue(query_payload["input"][0].endswith("Where is the evidence?"))
        self.assertEqual(embedder.model, "harrier-test::harrier-retrieval-v1")


if __name__ == "__main__":
    unittest.main()

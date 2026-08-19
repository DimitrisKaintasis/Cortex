from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from data_retrieval.domain.models import IngestionBundle
from data_retrieval.ingestion.chunker import TextChunker
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.large_ingestion import LargeFileIngestService
from data_retrieval.storage.memory import InMemoryRepository


class StagedMemoryRepository(InMemoryRepository):
    def __init__(self) -> None:
        super().__init__()
        self.batch_sizes: list[int] = []

    def begin_staged_ingestion(self, *, document, tags) -> bool:
        if self.get_document(document.document_id) is not None:
            return False
        self.persist_ingestion(
            IngestionBundle(document=document, atoms=(), tags=tags, atom_tags=())
        )
        return True

    def append_staged_ingestion(self, *, atoms, atom_tags) -> None:
        self.batch_sizes.append(len(atoms))
        document = self._documents[atoms[0].document_id]
        self.persist_ingestion(
            IngestionBundle(
                document=document,
                atoms=atoms,
                tags=(),
                atom_tags=atom_tags,
            )
        )

    def complete_staged_ingestion(self, *, document_id: str, atom_count: int) -> None:
        if self.get_document_atom_count(document_id) != atom_count:
            raise ValueError("atom count mismatch")


class LargeFileIngestionTests(unittest.TestCase):
    def test_staged_batches_match_regular_deterministic_ingestion(self) -> None:
        text = "A long source document with stable offsets and repeated material. " * 80
        chunker = TextChunker(max_chars=160, overlap_chars=25)
        reference = InMemoryRepository()
        expected = IngestService(reference, chunker).ingest_text(
            namespace="large",
            source="dataset.txt",
            text=text,
            explicit_tags=("Benchmark",),
        )

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.txt"
            path.write_text(text, encoding="utf-8", newline="")
            repository = StagedMemoryRepository()
            result = LargeFileIngestService(
                repository, chunker=chunker, batch_size=3
            ).ingest_path(
                path=path,
                namespace="large",
                source="dataset.txt",
                explicit_tags=("Benchmark",),
            )

        stored = repository.get_atoms_for_document(result.document_id)
        self.assertEqual(result.document_id, expected.document_id)
        self.assertEqual(tuple(atom.atom_id for atom in stored), expected.atom_ids)
        self.assertEqual(result.atom_count, len(expected.atom_ids))
        self.assertTrue(repository.batch_sizes)
        self.assertTrue(all(size <= 3 for size in repository.batch_sizes))

    def test_repeated_large_ingestion_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "dataset.txt"
            path.write_text("Repeatable content. " * 20, encoding="utf-8")
            repository = StagedMemoryRepository()
            service = LargeFileIngestService(repository, batch_size=2)
            first = service.ingest_path(
                path=path, namespace="large", source="dataset.txt"
            )
            second = service.ingest_path(
                path=path, namespace="large", source="dataset.txt"
            )

        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        self.assertEqual(second.atom_count, first.atom_count)


if __name__ == "__main__":
    unittest.main()

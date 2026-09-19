from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from data_retrieval.domain.models import AtomLinkRelation
from data_retrieval.mem0 import (
    Mem0BootstrapService,
    Mem0Entity,
    Mem0EntityRelationship,
    Mem0ProcessResult,
)
from data_retrieval.retrieval.models import AtomEmbedding
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.large_ingestion import LargeFileIngestService
from data_retrieval.storage.migrations import CURRENT_SCHEMA_VERSION
from data_retrieval.storage.postgresql import PostgreSQLRepository


class _PostgreSQLFakeMem0Processor:
    def __init__(self) -> None:
        self.call_count = 0

    def add(self, messages, *, user_id, run_id, metadata):
        self.call_count += 1
        source_atom_id = metadata["source_atom_ids"][0]
        return Mem0ProcessResult(
            entities=(
                Mem0Entity("postgresql", "PostgreSQL", (source_atom_id,)),
                Mem0Entity("mac", "Mac", (source_atom_id,)),
            ),
            relationships=(
                Mem0EntityRelationship(
                    "project-stack",
                    "postgresql",
                    "mac",
                    "used_with",
                    (source_atom_id,),
                ),
            ),
        )


@unittest.skipUnless(
    os.getenv("DATA_RETRIEVAL_TEST_POSTGRES_DSN"),
    "DATA_RETRIEVAL_TEST_POSTGRES_DSN is not configured",
)
class PostgreSQLRepositoryIntegrationTests(unittest.TestCase):
    def test_schema_version_is_recorded(self) -> None:
        with PostgreSQLRepository(os.environ["DATA_RETRIEVAL_TEST_POSTGRES_DSN"]) as repository:
            self.assertEqual(repository.schema_version, CURRENT_SCHEMA_VERSION)

    def test_mem0_bootstrap_lineage_and_resume(self) -> None:
        namespace = f"integration-mem0-{uuid4()}"
        with PostgreSQLRepository(os.environ["DATA_RETRIEVAL_TEST_POSTGRES_DSN"]) as repository:
            IngestService(repository).ingest_text(
                namespace=namespace,
                source="conversation",
                text="The project uses PostgreSQL and the Mac runs inference.",
                explicit_tags=("architecture",),
            )
            processor = _PostgreSQLFakeMem0Processor()
            service = Mem0BootstrapService(repository, processor)

            first = service.run(namespace=namespace)
            second = service.run(namespace=namespace)

            self.assertEqual(first.entities_imported, 2)
            self.assertEqual(first.entity_support_links_created, 2)
            self.assertEqual(first.entity_relationship_links_created, 1)
            self.assertEqual(second.batches_resumed, 1)
            self.assertEqual(processor.call_count, 1)
            links = repository.list_atom_links(
                namespace=namespace, relation=AtomLinkRelation.SUPPORTED_BY
            )
            self.assertEqual(len(links), 2)

    def test_ingestion_indexed_channels_and_staged_file(self) -> None:
        namespace = f"integration-{uuid4()}"
        with PostgreSQLRepository(os.environ["DATA_RETRIEVAL_TEST_POSTGRES_DSN"]) as repository:
            first = IngestService(repository).ingest_text(
                namespace=namespace,
                source="first",
                text="PostgreSQL stores weighted memory atoms.",
                explicit_tags=("database",),
            )
            IngestService(repository).ingest_text(
                namespace=namespace,
                source="second",
                text="A completely unrelated cooking note.",
            )
            repository.upsert_embeddings(
                (
                    AtomEmbedding(
                        atom_id=first.atom_ids[0],
                        provider="integration",
                        model="two-dimensions",
                        dimensions=2,
                        vector=(1.0, 0.0),
                        content_hash=repository.get_atom(first.atom_ids[0]).content_hash,
                    ),
                )
            )

            self.assertEqual(
                repository.search_tag_hits(
                    namespace=namespace, canonical_tags=("database",), limit=5
                )[0].atom_id,
                first.atom_ids[0],
            )
            self.assertEqual(
                repository.search_lexical_hits(
                    namespace=namespace, query="weighted memory", limit=5
                )[0].atom_id,
                first.atom_ids[0],
            )
            self.assertEqual(
                repository.search_semantic_hits(
                    namespace=namespace,
                    provider="integration",
                    model="two-dimensions",
                    query_vector=(1.0, 0.0),
                    limit=5,
                )[0].atom_id,
                first.atom_ids[0],
            )

            with tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / "large.txt"
                path.write_text("Batched source material. " * 200, encoding="utf-8")
                staged = LargeFileIngestService(repository, batch_size=4).ingest_path(
                    path=path,
                    namespace=namespace,
                    source="large.txt",
                )
            self.assertGreater(staged.atom_count, 1)
            self.assertEqual(
                repository.get_document_atom_count(staged.document_id), staged.atom_count
            )


if __name__ == "__main__":
    unittest.main()

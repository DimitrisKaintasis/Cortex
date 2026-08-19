from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from data_retrieval.domain.models import AtomLink, AtomLinkRelation, IngestionBundle
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.sqlite import SQLiteRepository


class SQLiteMigrationTests(unittest.TestCase):
    def test_pre_retrieval_schema_is_upgraded_in_place(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "old.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE atom_tags (
                    atom_id TEXT NOT NULL,
                    tag_id TEXT NOT NULL,
                    weight_raw REAL NOT NULL,
                    confidence REAL NOT NULL,
                    origin TEXT NOT NULL,
                    evidence_sources_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(atom_id, tag_id)
                );
                CREATE TABLE atom_links (
                    from_atom_id TEXT NOT NULL,
                    to_atom_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    PRIMARY KEY(from_atom_id, to_atom_id, relation)
                );
                """
            )
            connection.close()

            with SQLiteRepository(path) as repository:
                first = IngestService(repository).ingest_text(
                    namespace="migration",
                    source="first",
                    text="The old database remains usable.",
                    explicit_tags=("migration",),
                )
                second = IngestService(repository).ingest_text(
                    namespace="migration",
                    source="second",
                    text="New learned link fields are available.",
                )
                document = repository.get_document(second.document_id)
                self.assertIsNotNone(document)
                repository.persist_ingestion(
                    IngestionBundle(
                        document=document,
                        atoms=repository.get_atoms_for_document(second.document_id),
                        tags=(),
                        atom_tags=(),
                        atom_links=(
                            AtomLink(
                                from_atom_id=second.atom_ids[0],
                                to_atom_id=first.atom_ids[0],
                                relation=AtomLinkRelation.CO_USED,
                                weight_raw=0.2,
                            ),
                        ),
                    )
                )

                tag_edge = repository.atom_tags_for(first.atom_ids[0])[0]
                learned_link = repository.get_atom_links(second.atom_ids[0])[0]

            self.assertIsNotNone(tag_edge.updated_at)
            self.assertEqual(learned_link.weight_raw, 0.2)
            self.assertIsNotNone(learned_link.updated_at)


if __name__ == "__main__":
    unittest.main()

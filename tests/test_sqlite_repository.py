import sqlite3
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    AtomRole,
    Document,
    IngestionBundle,
    PayloadModality,
)
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.temporal import TemporalBridge


class SQLiteRepositoryTests(unittest.TestCase):
    def test_ingestion_survives_close_and_reopen_with_complete_hydration(self) -> None:
        occurred_at = datetime(2026, 8, 19, 12, 30, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "canonical.sqlite3"
            repository = SQLiteRepository(path)
            result = IngestService(repository).ingest_text(
                namespace="project-a",
                source="notes/architecture.txt",
                text="Atoms, tags, weights, and lineage belong in one canonical store.",
                explicit_tags=("Architecture", "Data Retrieval"),
                occurred_at=occurred_at,
                metadata={"source_type": "note"},
            )
            original_document = repository.get_document(result.document_id)
            original_atoms = repository.get_atoms_for_document(result.document_id)
            original_edges = repository.atom_tags_for(result.atom_ids[0])
            repository.close()

            reopened = SQLiteRepository(path)
            self.assertEqual(reopened.get_document(result.document_id), original_document)
            self.assertEqual(reopened.get_atoms_for_document(result.document_id), original_atoms)
            self.assertEqual(reopened.atom_tags_for(result.atom_ids[0]), original_edges)
            self.assertEqual(reopened.get_atom(result.atom_ids[0]).occurred_at, occurred_at)

            repeated = IngestService(reopened).ingest_text(
                namespace="project-a",
                source="notes/architecture.txt",
                text="Atoms, tags, weights, and lineage belong in one canonical store.",
                explicit_tags=("Architecture", "Data Retrieval"),
                occurred_at=occurred_at,
            )
            self.assertTrue(repeated.idempotent)
            self.assertEqual(reopened.document_count, 1)
            reopened.close()

    def test_temporal_summary_atoms_and_lineage_survive_reopen(self) -> None:
        start = datetime(2026, 8, 19, 12, tzinfo=UTC)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "canonical.sqlite3"
            repository = SQLiteRepository(path)
            source = IngestService(repository).ingest_text(
                namespace="project-a",
                source="conversation/1",
                text="Decision: use SQLite before adding a graph server.",
                occurred_at=start + timedelta(minutes=10),
            )
            source_atoms = repository.get_atoms_for_document(source.document_id)
            projection = TemporalBridge().project(
                namespace="project-a",
                timeline_id="main",
                atoms=source_atoms,
                timezone_name="UTC",
                range_start=start,
                range_end=start + timedelta(hours=1),
                state_path=root / "temporal-state.sqlite3",
            )
            repository.persist_ingestion(projection.bundle)
            six_hour_id = next(
                atom.atom_id
                for atom in projection.bundle.atoms
                if atom.metadata["granularity"] == "six_hour"
            )
            repository.close()

            reopened = SQLiteRepository(path)
            six_hour = reopened.get_atom(six_hour_id)
            links = reopened.get_atom_links(six_hour_id)
            self.assertEqual(six_hour.kind, AtomKind.TEMPORAL_SUMMARY)
            self.assertEqual(six_hour.role, AtomRole.DERIVED)
            self.assertEqual(six_hour.modality, PayloadModality.TEXT)
            self.assertEqual({link.to_atom_id for link in links}, set(source.atom_ids))
            self.assertTrue(all(link.relation is AtomLinkRelation.SUMMARIZES for link in links))
            reopened.close()

    def test_migrates_legacy_atom_kind_to_role_and_modality(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(path)
            connection.executescript(
                """
                CREATE TABLE documents (
                    document_id TEXT PRIMARY KEY,
                    namespace TEXT NOT NULL,
                    source TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                );
                CREATE TABLE atoms (
                    atom_id TEXT PRIMARY KEY,
                    document_id TEXT NOT NULL REFERENCES documents(document_id),
                    namespace TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    char_start INTEGER NOT NULL,
                    char_end INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    occurred_at TEXT,
                    created_at TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    UNIQUE(document_id, position)
                );
                INSERT INTO documents VALUES (
                    'doc-legacy', 'project-a', 'legacy', 'doc-hash',
                    '2026-08-19T12:00:00+00:00', '{}'
                );
                INSERT INTO atoms VALUES (
                    'atom-legacy', 'doc-legacy', 'project-a', 0, 0, 7,
                    'summary', 'atom-hash', 'temporal_summary', NULL,
                    '2026-08-19T12:00:00+00:00', '{}'
                );
                INSERT INTO atoms VALUES (
                    'atom-mem0', 'doc-legacy', 'project-a', 1, 8, 19,
                    'memory fact', 'mem0-hash', 'source', NULL,
                    '2026-08-19T12:00:00+00:00', '{"source_system":"mem0"}'
                );
                """
            )
            connection.commit()
            connection.close()

            with SQLiteRepository(path) as repository:
                atom = repository.get_atom("atom-legacy")
                self.assertEqual(atom.role, AtomRole.DERIVED)
                self.assertEqual(atom.modality, PayloadModality.TEXT)
                self.assertEqual(
                    {
                        item.atom_id
                        for item in repository.list_atoms(
                            namespace="project-a", role=AtomRole.DERIVED
                        )
                    },
                    {"atom-legacy", "atom-mem0"},
                )

    def test_foreign_key_failure_rolls_back_the_complete_bundle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            repository = SQLiteRepository(Path(directory) / "canonical.sqlite3")
            document = Document(
                document_id=stable_id("doc", "rollback"),
                namespace="project-a",
                source="rollback-test",
                content_hash=content_hash("rollback"),
            )
            atom = Atom(
                atom_id=stable_id("atom", "rollback"),
                document_id=document.document_id,
                namespace=document.namespace,
                position=0,
                char_start=0,
                char_end=8,
                content="rollback",
                content_hash=content_hash("rollback"),
            )
            invalid = IngestionBundle(
                document=document,
                atoms=(atom,),
                tags=(),
                atom_tags=(),
                atom_links=(
                    AtomLink(
                        from_atom_id=atom.atom_id,
                        to_atom_id="missing-atom",
                        relation=AtomLinkRelation.DERIVED_FROM,
                    ),
                ),
            )

            with self.assertRaises(sqlite3.IntegrityError):
                repository.persist_ingestion(invalid)

            self.assertEqual(repository.document_count, 0)
            self.assertEqual(repository.atom_count, 0)
            repository.close()


if __name__ == "__main__":
    unittest.main()

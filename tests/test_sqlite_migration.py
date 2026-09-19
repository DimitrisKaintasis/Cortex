from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from data_retrieval.domain.models import AtomLink, AtomLinkRelation, IngestionBundle
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.migrations import (
    CONNECTOR_TABLES,
    CURRENT_SCHEMA_VERSION,
    UnsupportedSchemaVersion,
)
from data_retrieval.storage.sqlite import SQLiteRepository


class _InterruptedMigrationRepository(SQLiteRepository):
    def _seed_weight_event_baselines(self) -> None:
        super()._seed_weight_event_baselines()
        raise RuntimeError("simulated migration interruption")


class _FailedInvariantRepository(SQLiteRepository):
    def _validate_schema_structure(self) -> None:
        raise RuntimeError("simulated invariant failure")


class _InterruptedConnectorMigrationRepository(SQLiteRepository):
    def _validate_connector_schema_structure(self) -> None:
        raise RuntimeError("simulated connector migration interruption")


class SQLiteMigrationTests(unittest.TestCase):
    def test_fresh_database_is_built_by_the_versioned_baseline_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fresh.sqlite3"

            with SQLiteRepository(path) as repository:
                self.assertEqual(repository.schema_version, CURRENT_SCHEMA_VERSION)

            connection = sqlite3.connect(path)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            connection.close()

            self.assertEqual(version, CURRENT_SCHEMA_VERSION)
            self.assertIn("documents", tables)
            self.assertIn("weight_events", tables)

    def test_current_database_startup_is_a_schema_no_op(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "current.sqlite3"
            with SQLiteRepository(path):
                pass

            connection = sqlite3.connect(path)
            before = int(connection.execute("PRAGMA schema_version").fetchone()[0])
            connection.close()

            with SQLiteRepository(path) as repository:
                self.assertEqual(repository.schema_version, CURRENT_SCHEMA_VERSION)

            connection = sqlite3.connect(path)
            after = int(connection.execute("PRAGMA schema_version").fetchone()[0])
            connection.close()
            self.assertEqual(after, before)

    def test_future_database_version_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "future.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION + 1}")
            connection.close()

            with self.assertRaisesRegex(UnsupportedSchemaVersion, "newer than supported"):
                SQLiteRepository(path)

    def test_current_version_with_missing_schema_fails_invariant_check(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "invalid.sqlite3"
            connection = sqlite3.connect(path)
            connection.execute(f"PRAGMA user_version = {CURRENT_SCHEMA_VERSION}")
            connection.close()

            with self.assertRaisesRegex(RuntimeError, "missing tables"):
                SQLiteRepository(path)

    def test_interrupted_migration_rolls_back_schema_and_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "interrupted.sqlite3"

            with self.assertRaisesRegex(RuntimeError, "simulated migration interruption"):
                _InterruptedMigrationRepository(path)

            connection = sqlite3.connect(path)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            connection.close()

            self.assertEqual(version, 0)
            self.assertEqual(tables, [])

    def test_failed_invariant_does_not_record_the_new_version(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "failed-invariant.sqlite3"

            with self.assertRaisesRegex(RuntimeError, "simulated invariant failure"):
                _FailedInvariantRepository(path)

            connection = sqlite3.connect(path)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
            connection.close()

            self.assertEqual(version, 0)
            self.assertEqual(tables, [])

    def test_interrupted_connector_migration_rolls_back_version_two(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "interrupted-connector.sqlite3"

            with self.assertRaisesRegex(
                RuntimeError, "simulated connector migration interruption"
            ):
                _InterruptedConnectorMigrationRepository(path)

            connection = sqlite3.connect(path)
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            connection.close()

            self.assertEqual(version, 1)
            self.assertTrue(CONNECTOR_TABLES.isdisjoint(tables))

    def test_version_one_database_is_backed_up_before_connector_migration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "version-one.sqlite3"
            with SQLiteRepository(path):
                pass
            connection = sqlite3.connect(path)
            for table in (
                "connector_sync_batches",
                "connector_relations",
                "connector_tombstones",
                "connector_records",
                "connector_record_objects",
                "connector_sync_runs",
                "connector_sources",
            ):
                connection.execute(f"DROP TABLE {table}")
            connection.execute("PRAGMA user_version = 1")
            connection.commit()
            connection.close()

            with SQLiteRepository(path) as repository:
                backup_path = repository.last_migration_backup
                self.assertEqual(repository.schema_version, CURRENT_SCHEMA_VERSION)
                self.assertIsNotNone(backup_path)

            assert backup_path is not None
            backup = sqlite3.connect(backup_path)
            backup_version = int(backup.execute("PRAGMA user_version").fetchone()[0])
            backup_tables = {
                str(row[0])
                for row in backup.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            backup.close()
            self.assertEqual(backup_version, 1)
            self.assertTrue(CONNECTOR_TABLES.isdisjoint(backup_tables))

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
                self.assertEqual(repository.schema_version, CURRENT_SCHEMA_VERSION)
                backup_path = repository.last_migration_backup
                self.assertIsNotNone(backup_path)
                assert backup_path is not None
                self.assertTrue(backup_path.is_file())
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

            backup_connection = sqlite3.connect(backup_path)
            backup_version = int(backup_connection.execute("PRAGMA user_version").fetchone()[0])
            backup_tables = {
                str(row[0])
                for row in backup_connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            backup_connection.close()

            self.assertEqual(backup_version, 0)
            self.assertEqual(backup_tables, {"atom_tags", "atom_links"})
            self.assertIsNotNone(tag_edge.updated_at)
            self.assertEqual(learned_link.weight_raw, 0.2)
            self.assertIsNotNone(learned_link.updated_at)


if __name__ == "__main__":
    unittest.main()

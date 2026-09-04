from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from data_retrieval.operations.postgres_backups import (
    BackupCommandError,
    DockerPostgresBackupManager,
)


class _FakeRunner:
    def __init__(
        self, *, fail_restore: bool = False, legacy: bool = False, vector: bool = True
    ) -> None:
        self.commands: list[tuple[str, ...]] = []
        self.fail_restore = fail_restore
        self.vector = vector
        self.counts = {"documents": 2, "atoms": 7, "tags": 3, "atom_tags": 8}
        if not legacy:
            self.counts["weight_events"] = 4

    def capture(self, command: tuple[str, ...]) -> str:
        self.commands.append(command)
        if "ps" in command and "--quiet" in command:
            return "container-123"
        if "psql" in command:
            if "information_schema.tables" in command[-1]:
                return json.dumps({"vector_extension": self.vector, "tables": list(self.counts)})
            return json.dumps(self.counts)
        return ""

    def to_file(self, command: tuple[str, ...], path: Path) -> None:
        self.commands.append(command)
        path.write_bytes(b"PGDMP\x01deterministic-fixture")

    def from_file(self, command: tuple[str, ...], path: Path) -> str:
        self.commands.append(command)
        self.last_restore_bytes = path.read_bytes()
        if self.fail_restore:
            raise BackupCommandError("synthetic restore failure")
        return ""


class DockerPostgresBackupManagerTests(unittest.TestCase):
    def _manager(self, root: Path, runner: _FakeRunner) -> DockerPostgresBackupManager:
        compose = root / "compose.postgres.yml"
        compose.write_text("services: {}\n", encoding="utf-8")
        return DockerPostgresBackupManager(compose_file=compose, runner=runner)

    def test_backup_is_atomic_checksummed_and_contains_no_secret_arguments(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = _FakeRunner()
            manager = self._manager(root, runner)
            backup_path = root / "backups" / "snapshot.dump"

            result = manager.backup(backup_path)

            self.assertTrue(backup_path.is_file())
            manifest = json.loads(Path(result.manifest_path).read_text(encoding="utf-8"))
            self.assertEqual(manifest["sha256"], result.sha256)
            self.assertEqual(manifest["size_bytes"], backup_path.stat().st_size)
            command_arguments = (argument for command in runner.commands for argument in command)
            self.assertFalse(any("password" in argument.lower() for argument in command_arguments))
            pg_dump = next(command for command in runner.commands if "pg_dump" in command)
            self.assertIn("--format=custom", pg_dump)
            self.assertIn("--no-owner", pg_dump)

    def test_verify_restores_to_temporary_database_and_drops_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = _FakeRunner()
            manager = self._manager(root, runner)
            backup_path = root / "snapshot.dump"
            manager.backup(backup_path)

            result = manager.verify(backup_path)

            self.assertTrue(result.manifest_verified)
            self.assertTrue(result.vector_extension)
            self.assertEqual(result.row_counts["atoms"], 7)
            createdb = next(command for command in runner.commands if "createdb" in command)
            dropdb = next(command for command in runner.commands if "dropdb" in command)
            self.assertTrue(createdb[-1].startswith("data_retrieval_verify_"))
            self.assertEqual(createdb[-1], dropdb[-1])

    def test_verify_rejects_tampering_before_database_restore(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = _FakeRunner()
            manager = self._manager(root, runner)
            backup_path = root / "snapshot.dump"
            manager.backup(backup_path)
            backup_path.write_bytes(backup_path.read_bytes() + b"tampered")
            command_count = len(runner.commands)

            with self.assertRaisesRegex(BackupCommandError, "checksum"):
                manager.verify(backup_path)

            self.assertEqual(len(runner.commands), command_count)

    def test_failed_restore_still_drops_the_isolated_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = _FakeRunner(fail_restore=True)
            manager = self._manager(root, runner)
            backup_path = root / "snapshot.dump"
            manager.backup(backup_path)

            with self.assertRaisesRegex(BackupCommandError, "synthetic restore failure"):
                manager.verify(backup_path)

            self.assertTrue(any("dropdb" in command for command in runner.commands))

    def test_refuses_to_overwrite_an_existing_backup_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = _FakeRunner()
            manager = self._manager(root, runner)
            backup_path = root / "snapshot.dump"
            manager.backup(backup_path)

            with self.assertRaises(FileExistsError):
                manager.backup(backup_path)

    def test_legacy_backup_verifies_without_migrating_its_schema(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = _FakeRunner(legacy=True)
            manager = self._manager(root, runner)
            backup_path = root / "snapshot.dump"
            manager.backup(backup_path)

            result = manager.verify(backup_path)

            self.assertNotIn("weight_events", result.row_counts)
            query = [command[-1] for command in runner.commands if "psql" in command][-1]
            self.assertNotIn("weight_events", query)
            self.assertFalse(any("ALTER TABLE" in str(command) for command in runner.commands))

    def test_missing_extension_fails_and_cleans_up_restore_database(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            runner = _FakeRunner(vector=False)
            manager = self._manager(root, runner)
            backup_path = root / "snapshot.dump"
            manager.backup(backup_path)

            with self.assertRaisesRegex(BackupCommandError, "vector extension"):
                manager.verify(backup_path)

            self.assertTrue(any("dropdb" in command for command in runner.commands))


if __name__ == "__main__":
    unittest.main()

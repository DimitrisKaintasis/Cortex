from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol
from uuid import uuid4

_SAFE_IDENTIFIER = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_]*$")
_VERIFY_DATABASE_PREFIX = "data_retrieval_verify_"


class BackupCommandError(RuntimeError):
    """A Docker or PostgreSQL backup command failed."""


class CommandRunner(Protocol):
    def capture(self, command: tuple[str, ...]) -> str: ...

    def to_file(self, command: tuple[str, ...], path: Path) -> None: ...

    def from_file(self, command: tuple[str, ...], path: Path) -> str: ...


class SubprocessCommandRunner:
    """Run fixed argument vectors without invoking a shell."""

    def capture(self, command: tuple[str, ...]) -> str:
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self._check(result, command)
        return result.stdout.strip()

    def to_file(self, command: tuple[str, ...], path: Path) -> None:
        with path.open("wb") as output:
            result = subprocess.run(command, stdout=output, stderr=subprocess.PIPE, check=False)
            output.flush()
            os.fsync(output.fileno())
        self._check(result, command)

    def from_file(self, command: tuple[str, ...], path: Path) -> str:
        with path.open("rb") as source:
            result = subprocess.run(
                command,
                stdin=source,
                capture_output=True,
                check=False,
            )
        self._check(result, command)
        return self._decode(result.stdout).strip()

    @classmethod
    def _check(
        cls,
        result: subprocess.CompletedProcess[Any],
        command: tuple[str, ...],
    ) -> None:
        if result.returncode == 0:
            return
        stderr = cls._decode(result.stderr).strip()
        executable = Path(command[0]).name
        detail = stderr or f"exit code {result.returncode}"
        raise BackupCommandError(f"{executable} command failed: {detail}")

    @staticmethod
    def _decode(value: str | bytes | None) -> str:
        if value is None:
            return ""
        return value.decode("utf-8", errors="replace") if isinstance(value, bytes) else value


@dataclass(frozen=True, slots=True)
class BackupResult:
    backup_path: str
    manifest_path: str
    database: str
    created_at: str
    size_bytes: int
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RestoreVerification:
    backup_path: str
    verified_at: str
    sha256: str
    manifest_verified: bool
    vector_extension: bool
    row_counts: dict[str, int]

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


class DockerPostgresBackupManager:
    """Create and restore-test logical backups of the laptop PostgreSQL container."""

    def __init__(
        self,
        *,
        compose_file: Path = Path("compose.postgres.yml"),
        service: str = "postgres",
        database: str = "data_retrieval",
        database_user: str = "data_retrieval",
        runner: CommandRunner | None = None,
    ) -> None:
        for label, value in (
            ("service", service),
            ("database", database),
            ("database user", database_user),
        ):
            if not _SAFE_IDENTIFIER.fullmatch(value):
                raise ValueError(f"{label} must be a safe PostgreSQL identifier")
        self.compose_file = compose_file
        self.service = service
        self.database = database
        self.database_user = database_user
        self.runner = runner or SubprocessCommandRunner()

    def backup(self, output_path: Path, *, overwrite: bool = False) -> BackupResult:
        if not self.compose_file.is_file():
            raise ValueError(f"Compose file does not exist: {self.compose_file}")
        output_path = output_path.resolve()
        if output_path.exists() and not overwrite:
            raise FileExistsError(f"backup already exists: {output_path}")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        partial_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.partial")
        container_id = self._container_id()
        command = (
            "docker",
            "exec",
            container_id,
            "pg_dump",
            "--format=custom",
            "--compress=6",
            "--no-owner",
            "--no-privileges",
            f"--dbname={self.database}",
            f"--username={self.database_user}",
        )
        try:
            self.runner.to_file(command, partial_path)
            if not partial_path.is_file() or partial_path.stat().st_size == 0:
                raise BackupCommandError("pg_dump produced an empty backup")
            if output_path.exists() and not overwrite:
                raise FileExistsError(f"backup already exists: {output_path}")
            partial_path.replace(output_path)
        finally:
            partial_path.unlink(missing_ok=True)

        created_at = datetime.now(UTC).isoformat()
        digest = _sha256(output_path)
        result = BackupResult(
            backup_path=str(output_path),
            manifest_path=str(_manifest_path(output_path)),
            database=self.database,
            created_at=created_at,
            size_bytes=output_path.stat().st_size,
            sha256=digest,
        )
        _write_json_atomic(_manifest_path(output_path), result.as_dict())
        return result

    def verify(self, backup_path: Path) -> RestoreVerification:
        if not self.compose_file.is_file():
            raise ValueError(f"Compose file does not exist: {self.compose_file}")
        backup_path = backup_path.resolve()
        if not backup_path.is_file():
            raise FileNotFoundError(f"backup does not exist: {backup_path}")
        digest = _sha256(backup_path)
        manifest_verified = self._verify_manifest(backup_path, digest)
        container_id = self._container_id()
        verification_database = f"{_VERIFY_DATABASE_PREFIX}{uuid4().hex}"
        created = False
        try:
            self.runner.capture(
                (
                    "docker",
                    "exec",
                    container_id,
                    "createdb",
                    "--template=template0",
                    f"--username={self.database_user}",
                    verification_database,
                )
            )
            created = True
            self.runner.from_file(
                (
                    "docker",
                    "exec",
                    "--interactive",
                    container_id,
                    "pg_restore",
                    "--exit-on-error",
                    "--no-owner",
                    "--no-privileges",
                    f"--username={self.database_user}",
                    f"--dbname={verification_database}",
                ),
                backup_path,
            )
            raw_summary = self.runner.capture(
                (
                    "docker",
                    "exec",
                    container_id,
                    "psql",
                    "--tuples-only",
                    "--no-align",
                    f"--username={self.database_user}",
                    f"--dbname={verification_database}",
                    "--command",
                    _verification_query(),
                )
            )
            summary = json.loads(raw_summary)
        finally:
            if created:
                self._drop_verification_database(container_id, verification_database)

        row_counts = summary.get("row_counts")
        if not isinstance(row_counts, dict):
            raise BackupCommandError("restore verification returned invalid row counts")
        return RestoreVerification(
            backup_path=str(backup_path),
            verified_at=datetime.now(UTC).isoformat(),
            sha256=digest,
            manifest_verified=manifest_verified,
            vector_extension=bool(summary.get("vector_extension")),
            row_counts={str(key): int(value) for key, value in row_counts.items()},
        )

    def _container_id(self) -> str:
        container_id = self.runner.capture(
            (
                "docker",
                "compose",
                "--file",
                str(self.compose_file.resolve()),
                "ps",
                "--quiet",
                self.service,
            )
        ).strip()
        if not container_id:
            raise BackupCommandError(
                f"Compose service {self.service!r} is not running; start it before backup"
            )
        if any(character.isspace() for character in container_id):
            raise BackupCommandError("Docker returned an invalid container identifier")
        return container_id

    def _verify_manifest(self, backup_path: Path, digest: str) -> bool:
        manifest_path = _manifest_path(backup_path)
        if not manifest_path.is_file():
            return False
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, UnicodeError) as error:
            raise BackupCommandError(f"backup manifest is invalid: {error}") from error
        if not isinstance(manifest, dict) or manifest.get("sha256") != digest:
            raise BackupCommandError("backup checksum does not match its manifest")
        if manifest.get("database") != self.database:
            raise BackupCommandError("backup manifest names a different database")
        return True

    def _drop_verification_database(self, container_id: str, database: str) -> None:
        if not database.startswith(_VERIFY_DATABASE_PREFIX) or not _SAFE_IDENTIFIER.fullmatch(
            database
        ):
            raise BackupCommandError("refusing to drop an unexpected database name")
        self.runner.capture(
            (
                "docker",
                "exec",
                container_id,
                "dropdb",
                "--if-exists",
                "--force",
                f"--username={self.database_user}",
                database,
            )
        )


def default_backup_path(root: Path = Path("data/backups/postgres")) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    return root / f"data-retrieval-{timestamp}.dump"


def _manifest_path(backup_path: Path) -> Path:
    return backup_path.with_name(f"{backup_path.name}.json")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.partial")
    try:
        temporary.write_text(
            json.dumps(payload, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _verification_query() -> str:
    return """
        SELECT json_build_object(
            'vector_extension', EXISTS(
                SELECT 1 FROM pg_extension WHERE extname = 'vector'
            ),
            'row_counts', json_build_object(
                'documents', (SELECT count(*) FROM data_retrieval.documents),
                'atoms', (SELECT count(*) FROM data_retrieval.atoms),
                'tags', (SELECT count(*) FROM data_retrieval.tags),
                'atom_tags', (SELECT count(*) FROM data_retrieval.atom_tags),
                'weight_events', (SELECT count(*) FROM data_retrieval.weight_events)
            )
        )::text;
    """.strip()

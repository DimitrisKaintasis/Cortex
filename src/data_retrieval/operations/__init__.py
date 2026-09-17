"""Operational helpers for recoverable local deployments."""

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from data_retrieval.operations.postgres_backups import (
        BackupResult,
        DockerPostgresBackupManager,
        RestoreVerification,
    )

__all__ = [
    "BackupResult",
    "DockerPostgresBackupManager",
    "RestoreVerification",
]


def __getattr__(name: str) -> Any:
    if name not in __all__:
        raise AttributeError(name)
    try:
        from data_retrieval.operations import postgres_backups
    except ModuleNotFoundError as error:
        if (error.name or "").split(".", maxsplit=1)[0] != "psycopg":
            raise
        raise ModuleNotFoundError(
            "PostgreSQL operations are optional; install them with "
            "'python -m pip install -e \".[postgres]\"'"
        ) from error
    return getattr(postgres_backups, name)

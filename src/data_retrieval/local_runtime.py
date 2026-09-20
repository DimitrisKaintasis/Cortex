from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

from data_retrieval.storage.repository import CortexRepository
from data_retrieval.storage.sqlite import SQLiteRepository


@dataclass(frozen=True, slots=True)
class LocalStorageConfig:
    """Storage selection shared by local REST and stdio MCP transports."""

    database_path: Path = Path("data.sqlite3")
    postgres_dsn: str | None = field(default=None, repr=False)

    @property
    def storage_kind(self) -> str:
        return "postgresql" if self.postgres_dsn else "sqlite"

    @contextmanager
    def open_repository(self) -> Iterator[CortexRepository]:
        if self.postgres_dsn:
            try:
                from data_retrieval.storage.postgresql import PostgreSQLRepository
            except ModuleNotFoundError as error:
                if (error.name or "").split(".", maxsplit=1)[0] not in {
                    "pgvector",
                    "psycopg",
                }:
                    raise
                raise RuntimeError(
                    "PostgreSQL dependencies are not installed; install them with "
                    "'python -m pip install -e \".[postgres]\"'"
                ) from error
            repository: CortexRepository = PostgreSQLRepository(self.postgres_dsn)
        else:
            repository = SQLiteRepository(self.database_path)
        try:
            yield repository
        finally:
            repository.close()  # type: ignore[attr-defined]

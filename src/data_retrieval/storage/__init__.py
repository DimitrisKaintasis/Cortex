"""Persistence contracts and adapters."""

from typing import TYPE_CHECKING, Any

from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.repository import Repository
from data_retrieval.storage.sqlite import SQLiteRepository

if TYPE_CHECKING:
    from data_retrieval.storage.postgresql import PostgreSQLRepository

__all__ = [
    "InMemoryRepository",
    "PostgreSQLRepository",
    "Repository",
    "SQLiteRepository",
]


def __getattr__(name: str) -> Any:
    if name != "PostgreSQLRepository":
        raise AttributeError(name)
    try:
        from data_retrieval.storage.postgresql import PostgreSQLRepository
    except ModuleNotFoundError as error:
        if (error.name or "").split(".", maxsplit=1)[0] not in {"pgvector", "psycopg"}:
            raise
        raise ModuleNotFoundError(
            "PostgreSQL storage is optional; install it with "
            "'python -m pip install -e \".[postgres]\"'"
        ) from error
    return PostgreSQLRepository

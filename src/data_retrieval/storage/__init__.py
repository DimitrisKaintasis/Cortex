"""Persistence contracts and adapters."""

from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.postgresql import PostgreSQLRepository
from data_retrieval.storage.repository import Repository
from data_retrieval.storage.sqlite import SQLiteRepository

__all__ = [
    "InMemoryRepository",
    "PostgreSQLRepository",
    "Repository",
    "SQLiteRepository",
]

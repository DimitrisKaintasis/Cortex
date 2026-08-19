"""Persistence contracts and adapters."""

from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.repository import Repository
from data_retrieval.storage.sqlite import SQLiteRepository

__all__ = ["InMemoryRepository", "Repository", "SQLiteRepository"]

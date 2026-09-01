from __future__ import annotations

from datetime import datetime
from pathlib import Path

from data_retrieval.domain.models import AtomRole
from data_retrieval.storage.repository import Repository
from data_retrieval.temporal.bridge import TemporalBridge, TemporalProjectionResult


class TemporalEnrichmentService:
    """Build and persist Temporal summary atoms from stored source atoms."""

    def __init__(self, repository: Repository, bridge: TemporalBridge) -> None:
        self.repository = repository
        self.bridge = bridge

    def enrich_range(
        self,
        *,
        namespace: str,
        timeline_id: str,
        timezone_name: str,
        range_start: datetime,
        range_end: datetime,
        state_path: Path,
        max_workers: int = 1,
    ) -> TemporalProjectionResult:
        candidates = self.repository.list_atoms(
            namespace=namespace,
            occurred_from=range_start,
            occurred_to=range_end,
            role=AtomRole.SOURCE,
        )
        atoms = tuple(
            atom
            for atom in candidates
            if atom.metadata.get("timeline_id", timeline_id) == timeline_id
        )
        if not atoms:
            raise ValueError(
                "no timestamped source atoms match this namespace, timeline, and range"
            )
        projection = self.bridge.project(
            namespace=namespace,
            timeline_id=timeline_id,
            atoms=atoms,
            timezone_name=timezone_name,
            range_start=range_start,
            range_end=range_end,
            state_path=state_path,
            max_workers=max_workers,
        )
        self.repository.persist_ingestion(projection.bundle)
        return projection

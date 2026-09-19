from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import datetime

from data_retrieval.connectors.codec import sync_batch_to_mapping
from data_retrieval.connectors.contracts import (
    ConnectorCapability,
    Record,
    RecordRef,
    Relation,
    Source,
    SourceRef,
    SyncBatch,
    SyncBatchAcknowledgement,
    SyncCommitAcknowledgement,
    SyncRun,
)
from data_retrieval.domain.models import utc_now
from data_retrieval.storage.repository import ConnectorLifecycleRepository


class ConnectorSyncService:
    """Application service for durable, replay-safe external source synchronization."""

    def __init__(
        self,
        repository: ConnectorLifecycleRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.clock = clock

    def register_source(self, source: Source) -> Source:
        return self.repository.register_connector_source(source)

    def get_source(self, source: SourceRef) -> Source | None:
        return self.repository.get_connector_source(source)

    def submit_batch(self, batch: SyncBatch) -> SyncBatchAcknowledgement:
        source = self.repository.get_connector_source(batch.run.source)
        if source is None:
            raise ValueError("connector source is not registered")
        if ConnectorCapability.SOURCE_SYNC not in source.capabilities:
            raise ValueError("connector source does not allow source_sync")
        for scope in (
            *(record.scope for record in batch.records),
            *(relation.scope for relation in batch.relations),
        ):
            for name in ("organization_id", "project_id", "user_id", "device_id"):
                owner_value = getattr(source.owner_scope, name)
                item_value = getattr(scope, name)
                if owner_value is not None and item_value != owner_value:
                    raise ValueError(f"connector item scope is outside source owner {name}")
        return self.repository.apply_connector_sync_batch(
            batch=batch,
            fingerprint=_batch_fingerprint(batch),
            acknowledged_at=self.clock(),
        )

    def commit(
        self, *, request_id: str, run_request_id: str
    ) -> SyncCommitAcknowledgement:
        if not request_id.strip():
            raise ValueError("sync commit request_id cannot be empty")
        return self.repository.commit_connector_sync(
            request_id=request_id,
            run_request_id=run_request_id,
            committed_at=self.clock(),
        )

    def get_run(self, request_id: str) -> SyncRun | None:
        return self.repository.get_connector_sync_run(request_id)

    def get_record(self, record: RecordRef) -> Record | None:
        return self.repository.get_connector_record(record)

    def get_current_record(self, *, source: SourceRef, external_id: str) -> Record | None:
        return self.repository.get_current_connector_record(
            source=source, external_id=external_id
        )

    def get_record_predecessor(self, record: RecordRef) -> RecordRef | None:
        return self.repository.get_connector_record_predecessor(record)

    def get_relation(
        self, *, source: SourceRef, relation_id: str, relation_version: str
    ) -> Relation | None:
        return self.repository.get_connector_relation(
            source=source,
            relation_id=relation_id,
            relation_version=relation_version,
        )

    def get_cursor(self, source: SourceRef) -> str | None:
        return self.repository.get_connector_cursor(source)


def _batch_fingerprint(batch: SyncBatch) -> str:
    payload = json.dumps(
        sync_batch_to_mapping(batch),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()

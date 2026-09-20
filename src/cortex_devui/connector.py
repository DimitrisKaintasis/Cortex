from __future__ import annotations

from cortex import (
    ConnectorSyncRejected,
    CortexClient,
    SyncCommitAcknowledgement,
    sync_source_batches,
)
from cortex_devui.mapper import DevUIMapper
from cortex_devui.models import DevUISnapshot

DevUISyncRejected = ConnectorSyncRejected


class DevUIConnector:
    """Reference orchestration over the public SDK; DevUI remains authoritative."""

    def __init__(self, client: CortexClient, mapper: DevUIMapper) -> None:
        self.client = client
        self.mapper = mapper

    def sync_snapshot(self, snapshot: DevUISnapshot) -> SyncCommitAcknowledgement:
        source = self.mapper.source()
        batches = self.mapper.batches(snapshot)
        return sync_source_batches(
            self.client,
            source=source,
            batches=batches,
            commit_request_id=f"{batches[0].run.request_id}:commit",
        )

from __future__ import annotations

from dataclasses import dataclass

from cortex import CortexClient, SyncBatchAcknowledgement, SyncCommitAcknowledgement
from cortex.testing import validate_source_sync
from cortex_devui.mapper import DevUIMapper
from cortex_devui.models import DevUISnapshot


@dataclass(frozen=True, slots=True)
class DevUISyncRejected(RuntimeError):
    acknowledgement: SyncBatchAcknowledgement

    def __str__(self) -> str:
        return (
            f"DevUI batch {self.acknowledgement.batch_id} was not fully accepted; "
            "the source cursor was not committed"
        )


class DevUIConnector:
    """Reference orchestration over the public SDK; DevUI remains authoritative."""

    def __init__(self, client: CortexClient, mapper: DevUIMapper) -> None:
        self.client = client
        self.mapper = mapper

    def sync_snapshot(self, snapshot: DevUISnapshot) -> SyncCommitAcknowledgement:
        source = self.mapper.source()
        batches = self.mapper.batches(snapshot)
        validate_source_sync(source, batches)
        self.client.register_source(source)
        session = self.client.sync(batches[0].run)
        for batch in batches:
            acknowledgement = session.submit_batch(batch)
            if not acknowledgement.complete:
                raise DevUISyncRejected(acknowledgement)
        return session.commit(request_id=f"{batches[0].run.request_id}:commit")

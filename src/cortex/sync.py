from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from cortex.client import CortexClient
from cortex.testing import validate_source_sync
from data_retrieval.connectors.contracts import (
    Source,
    SyncBatch,
    SyncBatchAcknowledgement,
    SyncCommitAcknowledgement,
)


@dataclass(frozen=True, slots=True)
class ConnectorSyncRejected(RuntimeError):
    """A batch was only partially accepted, so its source cursor was not committed."""

    acknowledgement: SyncBatchAcknowledgement

    def __str__(self) -> str:
        return (
            f"connector batch {self.acknowledgement.batch_id} was not fully accepted; "
            "the source cursor was not committed"
        )


def sync_source_batches(
    client: CortexClient,
    *,
    source: Source,
    batches: Sequence[SyncBatch],
    commit_request_id: str,
) -> SyncCommitAcknowledgement:
    """Validate, submit, and explicitly commit one connector-owned sync run."""

    report = validate_source_sync(source, batches)
    if report.run_count != 1:
        raise ValueError("sync_source_batches requires exactly one sync run")
    client.register_source(source)
    session = client.sync(batches[0].run)
    for batch in batches:
        acknowledgement = session.submit_batch(batch)
        if not acknowledgement.complete:
            raise ConnectorSyncRejected(acknowledgement)
    return session.commit(request_id=commit_request_id)

from __future__ import annotations

from cortex import (
    ConnectorSyncRejected,
    CortexClient,
    SyncCommitAcknowledgement,
    sync_source_batches,
)
from cortex_slack.mapper import SlackMapper
from cortex_slack.models import SlackEventPage

SlackSyncRejected = ConnectorSyncRejected


class SlackConnector:
    """Reference event-page orchestration; Slack remains authoritative."""

    def __init__(self, client: CortexClient, mapper: SlackMapper) -> None:
        self.client = client
        self.mapper = mapper

    def sync_page(self, page: SlackEventPage) -> SyncCommitAcknowledgement:
        source = self.mapper.source()
        batches = self.mapper.batches(page)
        return sync_source_batches(
            self.client,
            source=source,
            batches=batches,
            commit_request_id=f"{batches[0].run.request_id}:commit",
        )

    def sync_pages(
        self, pages: tuple[SlackEventPage, ...]
    ) -> tuple[SyncCommitAcknowledgement, ...]:
        return tuple(self.sync_page(page) for page in pages)

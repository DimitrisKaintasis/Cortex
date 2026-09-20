from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType

from cortex import (
    ConnectorCapability,
    ContextPack,
    ContributionPolicy,
    EvidenceResult,
    Record,
    RecordModality,
    RecordPayload,
    RecordRef,
    Relation,
    Scope,
    Source,
    SourceRef,
    SyncBatch,
    SyncMode,
    SyncRun,
    Tombstone,
    Visibility,
)
from cortex_slack.models import JsonValue, SlackEventPage, SlackMessage


@dataclass(frozen=True, slots=True)
class SlackConnectorConfig:
    organization_id: str
    workspace_id: str
    channel_projects: Mapping[str, str]
    connector_version: str = "0.1.0"
    batch_size: int = 500

    def __post_init__(self) -> None:
        for name, value in (
            ("organization_id", self.organization_id),
            ("workspace_id", self.workspace_id),
            ("connector_version", self.connector_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")
        projects = dict(self.channel_projects)
        if not projects:
            raise ValueError("channel_projects cannot be empty")
        for channel_id, project_id in projects.items():
            if not channel_id.strip() or not project_id.strip():
                raise ValueError("channel_projects requires non-empty channel and project IDs")
        object.__setattr__(self, "channel_projects", MappingProxyType(projects))

    @property
    def source_ref(self) -> SourceRef:
        return SourceRef("slack", f"workspace:{self.workspace_id}")

    @property
    def owner_scope(self) -> Scope:
        return Scope(
            visibility=Visibility.ORGANIZATION,
            organization_id=self.organization_id,
            contribution_policy=ContributionPolicy.PRIVATE,
        )

    def scope_for_channel(self, channel_id: str) -> Scope:
        project_id = self.channel_projects.get(channel_id)
        if project_id is None:
            raise ValueError(f"Slack channel is not mapped to a Cortex project: {channel_id}")
        return Scope(
            visibility=Visibility.PROJECT,
            organization_id=self.organization_id,
            project_id=project_id,
            contribution_policy=ContributionPolicy.PRIVATE,
        )


@dataclass(frozen=True, slots=True)
class SlackMessagePointer:
    evidence_id: str
    external_id: str
    external_version: str
    workspace_id: str
    channel_id: str
    message_ts: str
    thread_ts: str
    author_id: str
    project_id: str
    edited_ts: str | None = None


class SlackMapper:
    """Map authorized Slack event pages through the public Cortex contract."""

    def __init__(self, config: SlackConnectorConfig) -> None:
        self.config = config

    def source(self) -> Source:
        return Source(
            source=self.config.source_ref,
            connector_id="slack-reference",
            connector_version=self.config.connector_version,
            owner_scope=self.config.owner_scope,
            capabilities=(
                ConnectorCapability.SOURCE_SYNC,
                ConnectorCapability.CONTEXT_RETRIEVAL,
                ConnectorCapability.OUTCOME_REPORTING,
                ConnectorCapability.CHANGE_OBSERVATION,
            ),
            metadata={"source_kind": "mutable-conversation"},
        )

    def batches(self, page: SlackEventPage) -> tuple[SyncBatch, ...]:
        run = SyncRun(
            request_id=f"slack-sync:{page.page_id}",
            source=self.config.source_ref,
            mode=SyncMode.INCREMENTAL,
            started_at=page.received_at,
            previous_cursor=page.previous_cursor,
            proposed_cursor=page.next_cursor,
        )
        records = tuple(self._record(message) for message in page.messages)
        relations = tuple(
            relation
            for message in page.messages
            if (relation := self._reply_relation(message)) is not None
        )
        tombstones = []
        for deletion in page.deletions:
            self.config.scope_for_channel(deletion.channel_id)
            tombstones.append(
                Tombstone(
                    record=RecordRef(self.config.source_ref, deletion.external_id),
                    tombstone_version=f"deleted:{deletion.event_ts}",
                    observed_at=deletion.observed_at,
                    reason=deletion.reason,
                )
            )
        groups: list[
            tuple[tuple[Record, ...], tuple[Relation, ...], tuple[Tombstone, ...]]
        ] = []
        groups.extend((chunk, (), ()) for chunk in _chunks(records, self.config.batch_size))
        groups.extend(((), chunk, ()) for chunk in _chunks(relations, self.config.batch_size))
        groups.extend(
            ((), (), chunk)
            for chunk in _chunks(tuple(tombstones), self.config.batch_size)
        )
        return tuple(
            SyncBatch(
                batch_id=f"{run.request_id}:batch:{sequence}",
                sequence=sequence,
                run=run,
                records=batch_records,
                relations=batch_relations,
                tombstones=batch_tombstones,
            )
            for sequence, (batch_records, batch_relations, batch_tombstones) in enumerate(
                groups
            )
        )

    def pointer(self, evidence: EvidenceResult) -> SlackMessagePointer:
        if evidence.record.source != self.config.source_ref:
            raise ValueError("evidence does not belong to this Slack workspace")
        metadata = evidence.metadata
        channel_id = _metadata_text(metadata, "channel_id")
        message_ts = _metadata_text(metadata, "message_ts")
        expected = f"channel:{channel_id}:message:{message_ts}"
        if evidence.record.external_id != expected:
            raise ValueError("Slack evidence metadata conflicts with its external identity")
        scope = self.config.scope_for_channel(channel_id)
        if evidence.scope != scope:
            raise ValueError("Slack evidence scope conflicts with the channel allowlist")
        return SlackMessagePointer(
            evidence_id=evidence.evidence_id,
            external_id=evidence.record.external_id,
            external_version=evidence.record.external_version or "",
            workspace_id=self.config.workspace_id,
            channel_id=channel_id,
            message_ts=message_ts,
            thread_ts=_metadata_text(metadata, "thread_ts"),
            author_id=_metadata_text(metadata, "author_id"),
            project_id=scope.project_id or "",
            edited_ts=_optional_metadata_text(metadata, "edited_ts"),
        )

    def pointers(self, context: ContextPack) -> tuple[SlackMessagePointer, ...]:
        return tuple(
            self.pointer(item)
            for item in context.items
            if item.record.source == self.config.source_ref
        )

    def _record(self, message: SlackMessage) -> Record:
        scope = self.config.scope_for_channel(message.channel_id)
        metadata: dict[str, JsonValue] = dict(message.metadata)
        metadata.update(
            {
                "channel_id": message.channel_id,
                "message_ts": message.message_ts,
                "thread_ts": message.thread_ts or message.message_ts,
                "author_id": message.author_id,
            }
        )
        if message.edited_ts is not None:
            metadata["edited_ts"] = message.edited_ts
        return Record(
            ref=RecordRef(
                self.config.source_ref,
                message.external_id,
                message.external_version,
            ),
            modality=RecordModality.TEXT,
            payload=RecordPayload(inline=message.text),
            scope=scope,
            observed_at=message.observed_at,
            occurred_at=message.occurred_at,
            explicit_tags=message.tags,
            metadata=metadata,
        )

    def _reply_relation(self, message: SlackMessage) -> Relation | None:
        if message.thread_ts in {None, message.message_ts}:
            return None
        assert message.thread_root_version is not None
        scope = self.config.scope_for_channel(message.channel_id)
        return Relation(
            relation_id=(
                f"reply:{message.channel_id}:{message.message_ts}:{message.thread_ts}"
            ),
            relation_version=message.external_version,
            relation_type="reply_to",
            source=RecordRef(
                self.config.source_ref,
                message.external_id,
                message.external_version,
            ),
            target=RecordRef(
                self.config.source_ref,
                f"channel:{message.channel_id}:message:{message.thread_ts}",
                message.thread_root_version,
            ),
            scope=scope,
            observed_at=message.observed_at,
            occurred_at=message.occurred_at,
        )


def _chunks[T](values: tuple[T, ...], size: int) -> Iterable[tuple[T, ...]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _metadata_text(metadata: Mapping[str, JsonValue], key: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"Slack evidence metadata {key} must be non-empty text")
    return value


def _optional_metadata_text(
    metadata: Mapping[str, JsonValue], key: str
) -> str | None:
    value = metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str) or not value:
        raise ValueError(f"Slack evidence metadata {key} must be non-empty text")
    return value

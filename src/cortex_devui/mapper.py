from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

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
    Visibility,
)
from cortex_devui.models import DevUIEntity, DevUIEntityType, DevUISnapshot, JsonValue


@dataclass(frozen=True, slots=True)
class DevUIConnectorConfig:
    organization_id: str
    project_id: str
    source_instance: str
    connector_version: str = "0.1.0"
    batch_size: int = 500

    def __post_init__(self) -> None:
        for name, value in (
            ("organization_id", self.organization_id),
            ("project_id", self.project_id),
            ("source_instance", self.source_instance),
            ("connector_version", self.connector_version),
        ):
            if not value.strip():
                raise ValueError(f"{name} cannot be empty")
        if self.batch_size <= 0:
            raise ValueError("batch_size must be positive")

    @property
    def source_ref(self) -> SourceRef:
        return SourceRef("devui", self.source_instance)

    @property
    def scope(self) -> Scope:
        return Scope(
            visibility=Visibility.PROJECT,
            organization_id=self.organization_id,
            project_id=self.project_id,
            contribution_policy=ContributionPolicy.PRIVATE,
        )


@dataclass(frozen=True, slots=True)
class DevUIEntityPointer:
    evidence_id: str
    external_id: str
    external_version: str
    entity_type: DevUIEntityType
    locator: str
    file: str | None = None
    line: int | None = None
    display_name: str | None = None


class DevUIMapper:
    """Translate source-owned DevUI shapes to and from the public Cortex contract."""

    def __init__(self, config: DevUIConnectorConfig) -> None:
        self.config = config

    def source(self) -> Source:
        return Source(
            source=self.config.source_ref,
            connector_id="devui-reference",
            connector_version=self.config.connector_version,
            owner_scope=self.config.scope,
            capabilities=(
                ConnectorCapability.SOURCE_SYNC,
                ConnectorCapability.CONTEXT_RETRIEVAL,
                ConnectorCapability.OUTCOME_REPORTING,
            ),
            metadata={"source_kind": "structured-project"},
        )

    def batches(self, snapshot: DevUISnapshot) -> tuple[SyncBatch, ...]:
        run = SyncRun(
            request_id=f"devui-sync:{snapshot.snapshot_id}",
            source=self.config.source_ref,
            mode=SyncMode.FULL,
            started_at=snapshot.captured_at,
            proposed_cursor=snapshot.cursor,
        )
        versions = {
            entity.external_id: entity.external_version for entity in snapshot.entities
        }
        records = tuple(self._record(entity) for entity in snapshot.entities)
        relations = []
        for relation in snapshot.relations:
            if versions.get(relation.source_external_id) != relation.source_external_version:
                raise ValueError("DevUI relation source is missing from this snapshot version")
            if versions.get(relation.target_external_id) != relation.target_external_version:
                raise ValueError("DevUI relation target is missing from this snapshot version")
            relations.append(
                Relation(
                    relation_id=relation.relation_id,
                    relation_version=relation.relation_version,
                    relation_type=relation.relation_type,
                    source=RecordRef(
                        self.config.source_ref,
                        relation.source_external_id,
                        relation.source_external_version,
                    ),
                    target=RecordRef(
                        self.config.source_ref,
                        relation.target_external_id,
                        relation.target_external_version,
                    ),
                    scope=self.config.scope,
                    observed_at=relation.observed_at,
                    occurred_at=relation.occurred_at,
                    confidence=relation.confidence,
                    metadata=relation.metadata,
                )
            )

        groups: list[tuple[tuple[Record, ...], tuple[Relation, ...]]] = []
        groups.extend((chunk, ()) for chunk in _chunks(records, self.config.batch_size))
        groups.extend(
            ((), chunk)
            for chunk in _chunks(tuple(relations), self.config.batch_size)
        )
        return tuple(
            SyncBatch(
                batch_id=f"{run.request_id}:batch:{sequence}",
                sequence=sequence,
                run=run,
                records=batch_records,
                relations=batch_relations,
            )
            for sequence, (batch_records, batch_relations) in enumerate(groups)
        )

    def pointer(self, evidence: EvidenceResult) -> DevUIEntityPointer:
        if evidence.record.source != self.config.source_ref:
            raise ValueError("evidence does not belong to this DevUI source")
        kind_text, separator, locator = evidence.record.external_id.partition(":")
        if not separator or not locator:
            raise ValueError("DevUI evidence has an invalid external identity")
        try:
            entity_type = DevUIEntityType(kind_text)
        except ValueError as error:
            raise ValueError("DevUI evidence has an unknown entity type") from error
        metadata_type = evidence.metadata.get("entity_type")
        if metadata_type != entity_type.value:
            raise ValueError("DevUI evidence metadata conflicts with its external identity")
        return DevUIEntityPointer(
            evidence_id=evidence.evidence_id,
            external_id=evidence.record.external_id,
            external_version=evidence.record.external_version or "",
            entity_type=entity_type,
            locator=locator,
            file=_optional_metadata_text(evidence, "file"),
            line=_optional_metadata_int(evidence, "line"),
            display_name=_optional_metadata_text(evidence, "display_name"),
        )

    def pointers(self, context: ContextPack) -> tuple[DevUIEntityPointer, ...]:
        return tuple(
            self.pointer(item)
            for item in context.items
            if item.record.source == self.config.source_ref
        )

    def _record(self, entity: DevUIEntity) -> Record:
        metadata: dict[str, JsonValue] = dict(entity.metadata)
        metadata["entity_type"] = entity.entity_type.value
        if entity.display_name is not None:
            metadata["display_name"] = entity.display_name
        if entity.file is not None:
            metadata["file"] = entity.file
        if entity.line is not None:
            metadata["line"] = entity.line
        modality = (
            RecordModality.CODE
            if entity.entity_type in {DevUIEntityType.FILE, DevUIEntityType.FUNCTION}
            else RecordModality.TEXT
        )
        tags = tuple(dict.fromkeys((entity.entity_type.value, *entity.tags)))
        return Record(
            ref=RecordRef(
                self.config.source_ref,
                entity.external_id,
                entity.external_version,
            ),
            modality=modality,
            payload=RecordPayload(inline=entity.content),
            scope=self.config.scope,
            observed_at=entity.observed_at,
            occurred_at=entity.occurred_at,
            explicit_tags=tags,
            metadata=metadata,
        )


def _chunks[T](values: tuple[T, ...], size: int) -> Iterable[tuple[T, ...]]:
    for offset in range(0, len(values), size):
        yield values[offset : offset + size]


def _optional_metadata_text(evidence: EvidenceResult, key: str) -> str | None:
    value = evidence.metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"DevUI evidence metadata {key} must be text")
    return value


def _optional_metadata_int(evidence: EvidenceResult, key: str) -> int | None:
    value = evidence.metadata.get(key)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValueError(f"DevUI evidence metadata {key} must be an integer")
    return value

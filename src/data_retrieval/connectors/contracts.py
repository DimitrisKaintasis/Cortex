from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType

type JsonValue = (
    None | bool | int | float | str | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)
type Metadata = Mapping[str, JsonValue]


class ConnectorCapability(StrEnum):
    SOURCE_SYNC = "source_sync"
    CONTEXT_RETRIEVAL = "context_retrieval"
    OUTCOME_REPORTING = "outcome_reporting"
    CHANGE_OBSERVATION = "change_observation"


class Visibility(StrEnum):
    PRIVATE = "private"
    USER = "user"
    PROJECT = "project"
    ORGANIZATION = "organization"
    PUBLIC = "public"


class ContributionPolicy(StrEnum):
    PRIVATE = "private"
    AGGREGATE_ELIGIBLE = "aggregate_eligible"
    PUBLIC = "public"


class RecordModality(StrEnum):
    TEXT = "text"
    CODE = "code"
    EVENT = "event"
    IMAGE = "image"
    AUDIO = "audio"
    BINARY_REFERENCE = "binary_reference"


class SyncMode(StrEnum):
    FULL = "full"
    INCREMENTAL = "incremental"


class SyncItemType(StrEnum):
    RECORD = "record"
    RELATION = "relation"
    TOMBSTONE = "tombstone"


class TemporalQueryMode(StrEnum):
    AUTO = "auto"
    NONE = "none"
    CURRENT_STATE = "current_state"
    AS_OF = "as_of"
    RANGE = "range"
    HISTORY = "history"


class OutcomeValue(StrEnum):
    POSITIVE = "positive"
    NEGATIVE = "negative"


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} cannot be empty")


def _require_optional_text(name: str, value: str | None) -> None:
    if value is not None and not value.strip():
        raise ValueError(f"{name} cannot be empty when provided")


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must include a timezone")


def _require_unique(name: str, values: tuple[str, ...]) -> None:
    for value in values:
        _require_text(name, value)
    if len(values) != len(set(values)):
        raise ValueError(f"{name} values must be unique")


def _freeze_json(name: str, value: object) -> JsonValue:
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"{name} must contain only finite JSON numbers")
        return value
    if isinstance(value, Mapping):
        frozen: dict[str, JsonValue] = {}
        for key, item in value.items():
            if not isinstance(key, str):
                raise ValueError(f"{name} keys must be strings")
            frozen[key] = _freeze_json(f"{name}.{key}", item)
        return MappingProxyType(frozen)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return tuple(_freeze_json(f"{name}[{index}]", item) for index, item in enumerate(value))
    raise ValueError(f"{name} must contain only JSON-compatible values")


def _freeze_metadata(value: Mapping[str, JsonValue]) -> Metadata:
    frozen = _freeze_json("metadata", value)
    if not isinstance(frozen, Mapping):
        raise ValueError("metadata must be an object")
    return frozen


@dataclass(frozen=True, slots=True)
class Scope:
    """Requested ownership/visibility scope; authorization remains server-owned."""

    visibility: Visibility
    organization_id: str | None = None
    project_id: str | None = None
    user_id: str | None = None
    device_id: str | None = None
    contribution_policy: ContributionPolicy = ContributionPolicy.PRIVATE

    def __post_init__(self) -> None:
        for name, value in (
            ("organization_id", self.organization_id),
            ("project_id", self.project_id),
            ("user_id", self.user_id),
            ("device_id", self.device_id),
        ):
            _require_optional_text(name, value)
        if self.visibility is Visibility.ORGANIZATION and not self.organization_id:
            raise ValueError("organization visibility requires organization_id")
        if self.visibility is Visibility.PROJECT and not self.project_id:
            raise ValueError("project visibility requires project_id")
        if self.visibility is Visibility.USER and not self.user_id:
            raise ValueError("user visibility requires user_id")
        if self.visibility is Visibility.PRIVATE and not (self.user_id or self.device_id):
            raise ValueError("private visibility requires user_id or device_id")
        if self.contribution_policy is ContributionPolicy.PUBLIC:
            if self.visibility is not Visibility.PUBLIC:
                raise ValueError("public contribution requires public visibility")

    @property
    def key(self) -> tuple[str | None, ...]:
        return (
            self.organization_id,
            self.project_id,
            self.user_id,
            self.device_id,
            self.visibility.value,
            self.contribution_policy.value,
        )


@dataclass(frozen=True, slots=True)
class SourceRef:
    source_system: str
    source_instance: str

    def __post_init__(self) -> None:
        _require_text("source_system", self.source_system)
        _require_text("source_instance", self.source_instance)

    @property
    def key(self) -> tuple[str, str]:
        return (self.source_system, self.source_instance)


@dataclass(frozen=True, slots=True)
class Source:
    source: SourceRef
    connector_id: str
    connector_version: str
    owner_scope: Scope
    capabilities: tuple[ConnectorCapability, ...]
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("connector_id", self.connector_id)
        _require_text("connector_version", self.connector_version)
        if not self.capabilities:
            raise ValueError("source registration requires at least one capability")
        if len(self.capabilities) != len(set(self.capabilities)):
            raise ValueError("source capabilities must be unique")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class RecordRef:
    source: SourceRef
    external_id: str
    external_version: str | None = None

    def __post_init__(self) -> None:
        _require_text("external_id", self.external_id)
        _require_optional_text("external_version", self.external_version)

    @property
    def object_key(self) -> tuple[str, str, str]:
        return (*self.source.key, self.external_id)

    @property
    def version_key(self) -> tuple[str, str, str, str]:
        if self.external_version is None:
            raise ValueError("version_key requires external_version")
        return (*self.object_key, self.external_version)


@dataclass(frozen=True, slots=True)
class RecordPayload:
    inline: str | None = None
    reference_uri: str | None = None
    content_hash: str | None = None
    media_type: str | None = None

    def __post_init__(self) -> None:
        has_inline = self.inline is not None
        has_reference = self.reference_uri is not None
        if has_inline == has_reference:
            raise ValueError("payload requires exactly one of inline or reference_uri")
        if has_inline:
            assert self.inline is not None
            _require_text("payload inline", self.inline)
        if has_reference:
            assert self.reference_uri is not None
            _require_text("payload reference_uri", self.reference_uri)
            if not self.content_hash:
                raise ValueError("referenced payload requires content_hash")
        _require_optional_text("payload content_hash", self.content_hash)
        _require_optional_text("payload media_type", self.media_type)


@dataclass(frozen=True, slots=True)
class Record:
    ref: RecordRef
    modality: RecordModality
    payload: RecordPayload
    scope: Scope
    observed_at: datetime
    occurred_at: datetime | None = None
    explicit_tags: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.ref.external_version is None:
            raise ValueError("connector record requires external_version")
        _require_aware("observed_at", self.observed_at)
        _require_aware("occurred_at", self.occurred_at)
        _require_unique("explicit_tags", self.explicit_tags)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class Relation:
    relation_id: str
    relation_version: str
    relation_type: str
    source: RecordRef
    target: RecordRef
    scope: Scope
    observed_at: datetime
    occurred_at: datetime | None = None
    confidence: float = 1.0
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("relation_id", self.relation_id),
            ("relation_version", self.relation_version),
            ("relation_type", self.relation_type),
        ):
            _require_text(name, value)
        if self.source.external_version is None:
            raise ValueError("relation source requires external_version")
        _require_aware("observed_at", self.observed_at)
        _require_aware("occurred_at", self.occurred_at)
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("relation confidence must be finite and between 0 and 1")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))

    @property
    def version_key(self) -> tuple[str, str, str, str]:
        return (*self.source.source.key, self.relation_id, self.relation_version)


@dataclass(frozen=True, slots=True)
class Tombstone:
    record: RecordRef
    tombstone_version: str
    observed_at: datetime
    reason: str

    def __post_init__(self) -> None:
        if self.record.external_version is not None:
            raise ValueError("tombstone targets an external object, not one stored version")
        _require_text("tombstone_version", self.tombstone_version)
        _require_text("tombstone reason", self.reason)
        _require_aware("observed_at", self.observed_at)

    @property
    def version_key(self) -> tuple[str, str, str, str]:
        return (*self.record.object_key, self.tombstone_version)


@dataclass(frozen=True, slots=True)
class SyncRun:
    request_id: str
    source: SourceRef
    mode: SyncMode
    started_at: datetime
    previous_cursor: str | None = None
    proposed_cursor: str | None = None

    def __post_init__(self) -> None:
        _require_text("sync request_id", self.request_id)
        _require_aware("started_at", self.started_at)
        _require_optional_text("previous_cursor", self.previous_cursor)
        _require_optional_text("proposed_cursor", self.proposed_cursor)
        if self.mode is SyncMode.FULL and self.previous_cursor is not None:
            raise ValueError("full sync cannot have previous_cursor")


@dataclass(frozen=True, slots=True)
class SyncBatch:
    batch_id: str
    sequence: int
    run: SyncRun
    records: tuple[Record, ...] = ()
    relations: tuple[Relation, ...] = ()
    tombstones: tuple[Tombstone, ...] = ()

    def __post_init__(self) -> None:
        _require_text("sync batch_id", self.batch_id)
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise ValueError("sync batch sequence must be a non-negative integer")
        record_keys = tuple(record.ref.version_key for record in self.records)
        if len(record_keys) != len(set(record_keys)):
            raise ValueError("sync batch contains duplicate record versions")
        relation_keys = tuple(relation.version_key for relation in self.relations)
        if len(relation_keys) != len(set(relation_keys)):
            raise ValueError("sync batch contains duplicate relation versions")
        tombstone_keys = tuple(tombstone.version_key for tombstone in self.tombstones)
        if len(tombstone_keys) != len(set(tombstone_keys)):
            raise ValueError("sync batch contains duplicate tombstone versions")

        for record in self.records:
            if record.ref.source != self.run.source:
                raise ValueError("record source must match sync source")
        for relation in self.relations:
            if relation.source.source != self.run.source:
                raise ValueError("relation source must match sync source")
        for tombstone in self.tombstones:
            if tombstone.record.source != self.run.source:
                raise ValueError("tombstone source must match sync source")

        upserted_objects = {record.ref.object_key for record in self.records}
        tombstoned_objects = {tombstone.record.object_key for tombstone in self.tombstones}
        if upserted_objects & tombstoned_objects:
            raise ValueError("one sync batch cannot upsert and tombstone the same object")


@dataclass(frozen=True, slots=True)
class SyncItemFailure:
    item_type: SyncItemType
    item_id: str
    item_version: str
    code: str
    message: str
    retryable: bool

    def __post_init__(self) -> None:
        for name, value in (
            ("sync failure item_id", self.item_id),
            ("sync failure item_version", self.item_version),
            ("sync failure code", self.code),
            ("sync failure message", self.message),
        ):
            _require_text(name, value)

    @property
    def key(self) -> tuple[SyncItemType, str, str]:
        return (self.item_type, self.item_id, self.item_version)


@dataclass(frozen=True, slots=True)
class SyncBatchAcknowledgement:
    run_request_id: str
    batch_id: str
    sequence: int
    acknowledged_at: datetime
    accepted_records: int
    accepted_relations: int
    accepted_tombstones: int
    failures: tuple[SyncItemFailure, ...] = ()

    def __post_init__(self) -> None:
        _require_text("sync run_request_id", self.run_request_id)
        _require_text("sync batch_id", self.batch_id)
        if (
            not isinstance(self.sequence, int)
            or isinstance(self.sequence, bool)
            or self.sequence < 0
        ):
            raise ValueError("sync batch sequence must be a non-negative integer")
        _require_aware("acknowledged_at", self.acknowledged_at)
        for name, value in (
            ("accepted_records", self.accepted_records),
            ("accepted_relations", self.accepted_relations),
            ("accepted_tombstones", self.accepted_tombstones),
        ):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        failure_keys = tuple(failure.key for failure in self.failures)
        if len(failure_keys) != len(set(failure_keys)):
            raise ValueError("sync acknowledgement contains duplicate item failures")

    @property
    def complete(self) -> bool:
        return not self.failures


@dataclass(frozen=True, slots=True)
class SyncCommitAcknowledgement:
    request_id: str
    run_request_id: str
    source: SourceRef
    committed_cursor: str
    committed_at: datetime

    def __post_init__(self) -> None:
        _require_text("sync commit request_id", self.request_id)
        _require_text("sync commit run_request_id", self.run_request_id)
        _require_text("committed_cursor", self.committed_cursor)
        _require_aware("committed_at", self.committed_at)


@dataclass(frozen=True, slots=True)
class Query:
    request_id: str
    query: str
    scope: Scope
    top_k: int = 10
    budget_tokens: int | None = None
    timeline_id: str | None = None
    temporal_mode: TemporalQueryMode = TemporalQueryMode.AUTO
    as_of: datetime | None = None
    range_start: datetime | None = None
    range_end: datetime | None = None
    reference_time: datetime | None = None
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("query request_id", self.request_id)
        _require_text("query", self.query)
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        if self.budget_tokens is not None and self.budget_tokens <= 0:
            raise ValueError("budget_tokens must be positive when provided")
        _require_optional_text("timeline_id", self.timeline_id)
        _require_aware("as_of", self.as_of)
        _require_aware("range_start", self.range_start)
        _require_aware("range_end", self.range_end)
        _require_aware("reference_time", self.reference_time)
        if (self.range_start is None) != (self.range_end is None):
            raise ValueError("range_start and range_end must be provided together")
        if (
            self.range_start is not None
            and self.range_end is not None
            and self.range_start >= self.range_end
        ):
            raise ValueError("range_start must be before range_end")
        if self.temporal_mode is TemporalQueryMode.AS_OF and self.as_of is None:
            raise ValueError("as_of temporal mode requires as_of")
        if self.temporal_mode is TemporalQueryMode.RANGE and self.range_start is None:
            raise ValueError("range temporal mode requires range_start and range_end")
        if self.as_of is not None and self.temporal_mode not in {
            TemporalQueryMode.AS_OF,
            TemporalQueryMode.CURRENT_STATE,
        }:
            raise ValueError("as_of is valid only for as_of or current_state temporal mode")
        if self.range_start is not None and self.temporal_mode is not TemporalQueryMode.RANGE:
            raise ValueError("range bounds are valid only for range temporal mode")
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class EvidenceResult:
    evidence_id: str
    record: RecordRef
    modality: RecordModality
    content: str
    scope: Scope
    score: float
    score_evidence: tuple[str, ...] = ()
    lineage_evidence_ids: tuple[str, ...] = ()
    metadata: Metadata = field(default_factory=dict)

    def __post_init__(self) -> None:
        _require_text("evidence_id", self.evidence_id)
        if self.record.external_version is None:
            raise ValueError("evidence result requires external_version")
        _require_text("evidence content", self.content)
        if not math.isfinite(self.score) or self.score < 0.0:
            raise ValueError("evidence score must be finite and non-negative")
        _require_unique("score_evidence", self.score_evidence)
        _require_unique("lineage_evidence_ids", self.lineage_evidence_ids)
        object.__setattr__(self, "metadata", _freeze_metadata(self.metadata))


@dataclass(frozen=True, slots=True)
class ContextPack:
    retrieval_id: str
    query_request_id: str
    items: tuple[EvidenceResult, ...]
    low_confidence: bool
    budget_tokens: int | None = None
    used_tokens: int | None = None
    abstention_reason: str | None = None

    def __post_init__(self) -> None:
        _require_text("retrieval_id", self.retrieval_id)
        _require_text("query_request_id", self.query_request_id)
        evidence_ids = tuple(item.evidence_id for item in self.items)
        if len(evidence_ids) != len(set(evidence_ids)):
            raise ValueError("context pack evidence IDs must be unique")
        if self.budget_tokens is not None and self.budget_tokens <= 0:
            raise ValueError("budget_tokens must be positive when provided")
        if self.used_tokens is not None and self.used_tokens < 0:
            raise ValueError("used_tokens must be non-negative when provided")
        if (
            self.budget_tokens is not None
            and self.used_tokens is not None
            and self.used_tokens > self.budget_tokens
        ):
            raise ValueError("used_tokens cannot exceed budget_tokens")
        _require_optional_text("abstention_reason", self.abstention_reason)
        if not self.items and self.abstention_reason is None:
            raise ValueError("empty context pack requires abstention_reason")


@dataclass(frozen=True, slots=True)
class Outcome:
    request_id: str
    retrieval_id: str
    used_evidence_ids: tuple[str, ...]
    outcome: OutcomeValue
    occurred_at: datetime
    reason: str = ""

    def __post_init__(self) -> None:
        _require_text("outcome request_id", self.request_id)
        _require_text("retrieval_id", self.retrieval_id)
        if not self.used_evidence_ids:
            raise ValueError("outcome requires at least one used evidence ID")
        _require_unique("used_evidence_ids", self.used_evidence_ids)
        _require_aware("occurred_at", self.occurred_at)

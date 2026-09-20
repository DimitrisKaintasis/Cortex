from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Any

type JsonValue = (
    None | bool | int | float | str | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)


class DevUIEntityType(StrEnum):
    FILE = "file"
    FUNCTION = "function"
    MODULE = "module"
    PROPOSAL = "proposal"


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} cannot be empty")


def _require_aware(name: str, value: datetime | None) -> None:
    if value is not None and (value.tzinfo is None or value.utcoffset() is None):
        raise ValueError(f"{name} must include a timezone")


@dataclass(frozen=True, slots=True)
class DevUIEntity:
    external_id: str
    external_version: str
    entity_type: DevUIEntityType
    content: str
    observed_at: datetime
    occurred_at: datetime | None = None
    tags: tuple[str, ...] = ()
    display_name: str | None = None
    file: str | None = None
    line: int | None = None
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("external_id", self.external_id),
            ("external_version", self.external_version),
            ("content", self.content),
        ):
            _require_text(name, value)
        if not self.external_id.startswith(f"{self.entity_type.value}:"):
            raise ValueError("external_id must start with the DevUI entity type")
        _require_aware("observed_at", self.observed_at)
        _require_aware("occurred_at", self.occurred_at)
        if self.line is not None and self.line <= 0:
            raise ValueError("line must be positive when provided")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("tags must be unique")
        for tag in self.tags:
            _require_text("tag", tag)
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class DevUIRelation:
    relation_id: str
    relation_version: str
    relation_type: str
    source_external_id: str
    source_external_version: str
    target_external_id: str
    target_external_version: str
    observed_at: datetime
    occurred_at: datetime | None = None
    confidence: float = 1.0
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("relation_id", self.relation_id),
            ("relation_version", self.relation_version),
            ("relation_type", self.relation_type),
            ("source_external_id", self.source_external_id),
            ("source_external_version", self.source_external_version),
            ("target_external_id", self.target_external_id),
            ("target_external_version", self.target_external_version),
        ):
            _require_text(name, value)
        _require_aware("observed_at", self.observed_at)
        _require_aware("occurred_at", self.occurred_at)
        if not math.isfinite(self.confidence) or not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be finite and between zero and one")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))


@dataclass(frozen=True, slots=True)
class DevUISnapshot:
    snapshot_id: str
    cursor: str
    captured_at: datetime
    entities: tuple[DevUIEntity, ...]
    relations: tuple[DevUIRelation, ...] = ()

    def __post_init__(self) -> None:
        _require_text("snapshot_id", self.snapshot_id)
        _require_text("cursor", self.cursor)
        _require_aware("captured_at", self.captured_at)
        if not self.entities:
            raise ValueError("DevUI snapshot requires at least one entity")
        entity_keys = tuple(
            (entity.external_id, entity.external_version) for entity in self.entities
        )
        if len(entity_keys) != len(set(entity_keys)):
            raise ValueError("DevUI snapshot contains duplicate entity versions")
        relation_keys = tuple(
            (relation.relation_id, relation.relation_version)
            for relation in self.relations
        )
        if len(relation_keys) != len(set(relation_keys)):
            raise ValueError("DevUI snapshot contains duplicate relation versions")


def snapshot_from_mapping(value: object) -> DevUISnapshot:
    """Decode a source-native DevUI snapshot without importing Cortex internals."""

    root = _object("snapshot", value)
    _only(root, {"snapshot_id", "cursor", "captured_at", "entities", "relations"})
    entities = _array("entities", root.get("entities"))
    relations = _array("relations", root.get("relations", ()))
    return DevUISnapshot(
        snapshot_id=_text("snapshot_id", root.get("snapshot_id")),
        cursor=_text("cursor", root.get("cursor")),
        captured_at=_timestamp("captured_at", root.get("captured_at")),
        entities=tuple(_entity(item) for item in entities),
        relations=tuple(_relation(item) for item in relations),
    )


def _entity(value: object) -> DevUIEntity:
    item = _object("entity", value)
    _only(
        item,
        {
            "external_id",
            "external_version",
            "entity_type",
            "content",
            "observed_at",
            "occurred_at",
            "tags",
            "display_name",
            "file",
            "line",
            "metadata",
        },
    )
    tags = _array("tags", item.get("tags", ()))
    metadata = _object("metadata", item.get("metadata", {}))
    line = item.get("line")
    if line is not None and (not isinstance(line, int) or isinstance(line, bool)):
        raise ValueError("line must be an integer when provided")
    return DevUIEntity(
        external_id=_text("external_id", item.get("external_id")),
        external_version=_text("external_version", item.get("external_version")),
        entity_type=DevUIEntityType(_text("entity_type", item.get("entity_type"))),
        content=_text("content", item.get("content")),
        observed_at=_timestamp("observed_at", item.get("observed_at")),
        occurred_at=_optional_timestamp("occurred_at", item.get("occurred_at")),
        tags=tuple(_text("tag", tag) for tag in tags),
        display_name=_optional_text("display_name", item.get("display_name")),
        file=_optional_text("file", item.get("file")),
        line=line,
        metadata=metadata,
    )


def _relation(value: object) -> DevUIRelation:
    item = _object("relation", value)
    _only(
        item,
        {
            "relation_id",
            "relation_version",
            "relation_type",
            "source_external_id",
            "source_external_version",
            "target_external_id",
            "target_external_version",
            "observed_at",
            "occurred_at",
            "confidence",
            "metadata",
        },
    )
    confidence = item.get("confidence", 1.0)
    if not isinstance(confidence, (int, float)) or isinstance(confidence, bool):
        raise ValueError("confidence must be numeric")
    return DevUIRelation(
        relation_id=_text("relation_id", item.get("relation_id")),
        relation_version=_text("relation_version", item.get("relation_version")),
        relation_type=_text("relation_type", item.get("relation_type")),
        source_external_id=_text(
            "source_external_id", item.get("source_external_id")
        ),
        source_external_version=_text(
            "source_external_version", item.get("source_external_version")
        ),
        target_external_id=_text(
            "target_external_id", item.get("target_external_id")
        ),
        target_external_version=_text(
            "target_external_version", item.get("target_external_version")
        ),
        observed_at=_timestamp("observed_at", item.get("observed_at")),
        occurred_at=_optional_timestamp("occurred_at", item.get("occurred_at")),
        confidence=float(confidence),
        metadata=_object("metadata", item.get("metadata", {})),
    )


def _object(name: str, value: object) -> Mapping[str, Any]:
    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} must be an object with string keys")
    return value


def _array(name: str, value: object) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _only(value: Mapping[str, object], allowed: set[str]) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"unknown DevUI fields: {', '.join(unknown)}")


def _text(name: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be non-empty text")
    return value


def _optional_text(name: str, value: object) -> str | None:
    if value is None:
        return None
    return _text(name, value)


def _timestamp(name: str, value: object) -> datetime:
    text = _text(name, value)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 timestamp") from error
    _require_aware(name, parsed)
    return parsed


def _optional_timestamp(name: str, value: object) -> datetime | None:
    if value is None:
        return None
    return _timestamp(name, value)

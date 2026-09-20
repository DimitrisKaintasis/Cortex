from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from types import MappingProxyType
from typing import Any

type JsonValue = (
    None | bool | int | float | str | tuple["JsonValue", ...] | Mapping[str, "JsonValue"]
)


def _require_text(name: str, value: str) -> None:
    if not value.strip():
        raise ValueError(f"{name} cannot be empty")


def _require_aware(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must include a timezone")


@dataclass(frozen=True, slots=True)
class SlackMessage:
    channel_id: str
    message_ts: str
    text: str
    author_id: str
    observed_at: datetime
    occurred_at: datetime
    edited_ts: str | None = None
    thread_ts: str | None = None
    thread_root_version: str | None = None
    tags: tuple[str, ...] = ()
    metadata: Mapping[str, JsonValue] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for name, value in (
            ("channel_id", self.channel_id),
            ("message_ts", self.message_ts),
            ("text", self.text),
            ("author_id", self.author_id),
        ):
            _require_text(name, value)
        _require_aware("observed_at", self.observed_at)
        _require_aware("occurred_at", self.occurred_at)
        if self.edited_ts is not None:
            _require_text("edited_ts", self.edited_ts)
        if self.thread_ts is not None:
            _require_text("thread_ts", self.thread_ts)
        if self.thread_root_version is not None:
            _require_text("thread_root_version", self.thread_root_version)
        if self.thread_ts not in {None, self.message_ts} and self.thread_root_version is None:
            raise ValueError("Slack reply requires thread_root_version")
        if len(self.tags) != len(set(self.tags)):
            raise ValueError("Slack message tags must be unique")
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def external_id(self) -> str:
        return f"channel:{self.channel_id}:message:{self.message_ts}"

    @property
    def external_version(self) -> str:
        return f"edited:{self.edited_ts or '0'}"


@dataclass(frozen=True, slots=True)
class SlackDeletion:
    channel_id: str
    message_ts: str
    event_ts: str
    observed_at: datetime
    reason: str = "source_deleted"

    def __post_init__(self) -> None:
        for name, value in (
            ("channel_id", self.channel_id),
            ("message_ts", self.message_ts),
            ("event_ts", self.event_ts),
            ("reason", self.reason),
        ):
            _require_text(name, value)
        _require_aware("observed_at", self.observed_at)

    @property
    def external_id(self) -> str:
        return f"channel:{self.channel_id}:message:{self.message_ts}"


@dataclass(frozen=True, slots=True)
class SlackEventPage:
    page_id: str
    previous_cursor: str | None
    next_cursor: str
    received_at: datetime
    messages: tuple[SlackMessage, ...] = ()
    deletions: tuple[SlackDeletion, ...] = ()

    def __post_init__(self) -> None:
        _require_text("page_id", self.page_id)
        _require_text("next_cursor", self.next_cursor)
        _require_aware("received_at", self.received_at)
        if self.previous_cursor is not None:
            _require_text("previous_cursor", self.previous_cursor)
        if not self.messages and not self.deletions:
            raise ValueError("Slack event page cannot be empty")
        versions = tuple(
            (message.external_id, message.external_version) for message in self.messages
        )
        if len(versions) != len(set(versions)):
            raise ValueError("Slack event page contains duplicate message versions")
        tombstones = tuple(
            (deletion.external_id, deletion.event_ts) for deletion in self.deletions
        )
        if len(tombstones) != len(set(tombstones)):
            raise ValueError("Slack event page contains duplicate deletions")


def event_pages_from_mapping(value: object) -> tuple[SlackEventPage, ...]:
    root = _object("Slack export", value)
    _only(root, {"pages"})
    pages = _array("pages", root.get("pages"))
    if not pages:
        raise ValueError("Slack export requires at least one event page")
    decoded = tuple(_page(page) for page in pages)
    for previous, current in zip(decoded, decoded[1:], strict=False):
        if current.previous_cursor != previous.next_cursor:
            raise ValueError("Slack event page cursors must form a contiguous chain")
    return decoded


def _page(value: object) -> SlackEventPage:
    item = _object("Slack event page", value)
    _only(
        item,
        {
            "page_id",
            "previous_cursor",
            "next_cursor",
            "received_at",
            "messages",
            "deletions",
        },
    )
    return SlackEventPage(
        page_id=_text("page_id", item.get("page_id")),
        previous_cursor=_optional_text("previous_cursor", item.get("previous_cursor")),
        next_cursor=_text("next_cursor", item.get("next_cursor")),
        received_at=_timestamp("received_at", item.get("received_at")),
        messages=tuple(
            _message(message)
            for message in _array("messages", item.get("messages", ()))
        ),
        deletions=tuple(
            _deletion(deletion)
            for deletion in _array("deletions", item.get("deletions", ()))
        ),
    )


def _message(value: object) -> SlackMessage:
    item = _object("Slack message", value)
    _only(
        item,
        {
            "channel_id",
            "message_ts",
            "text",
            "author_id",
            "observed_at",
            "occurred_at",
            "edited_ts",
            "thread_ts",
            "thread_root_version",
            "tags",
            "metadata",
        },
    )
    return SlackMessage(
        channel_id=_text("channel_id", item.get("channel_id")),
        message_ts=_text("message_ts", item.get("message_ts")),
        text=_text("text", item.get("text")),
        author_id=_text("author_id", item.get("author_id")),
        observed_at=_timestamp("observed_at", item.get("observed_at")),
        occurred_at=_timestamp("occurred_at", item.get("occurred_at")),
        edited_ts=_optional_text("edited_ts", item.get("edited_ts")),
        thread_ts=_optional_text("thread_ts", item.get("thread_ts")),
        thread_root_version=_optional_text(
            "thread_root_version", item.get("thread_root_version")
        ),
        tags=tuple(
            _text("tag", tag) for tag in _array("tags", item.get("tags", ()))
        ),
        metadata=_object("metadata", item.get("metadata", {})),
    )


def _deletion(value: object) -> SlackDeletion:
    item = _object("Slack deletion", value)
    _only(item, {"channel_id", "message_ts", "event_ts", "observed_at", "reason"})
    return SlackDeletion(
        channel_id=_text("channel_id", item.get("channel_id")),
        message_ts=_text("message_ts", item.get("message_ts")),
        event_ts=_text("event_ts", item.get("event_ts")),
        observed_at=_timestamp("observed_at", item.get("observed_at")),
        reason=_optional_text("reason", item.get("reason")) or "source_deleted",
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
        raise ValueError(f"unknown Slack fields: {', '.join(unknown)}")


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

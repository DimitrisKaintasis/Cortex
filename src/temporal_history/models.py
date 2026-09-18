from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Granularity = Literal["pressure", "six_hour", "day", "week", "month", "year"]
ScopeType = Literal["channel", "thread"]
SummaryReason = Literal["scheduled", "pressure", "repair", "backfill"]
ActorType = Literal["human", "bot", "app", "agent", "system", "unknown"]


class NormalizedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    timeline_id: str
    event_type: str = "message"
    occurred_at: datetime
    recorded_at: datetime
    source_order: str
    actor_id: str | None = None
    actor_display_name: str | None = None
    actor_type: ActorType = "unknown"
    actor_resolution_source: Literal["slack_api", "export", "cache", "unresolved"] = (
        "unresolved"
    )
    actor_resolution_timestamp: datetime | None = None
    actor_is_active: bool | None = None
    raw_content: str
    display_content: str
    source_message_id: str
    thread_root_id: str | None = None
    is_thread_reply: bool = False
    thread_root_excerpt: str | None = None
    parent_event_id: str | None = None
    supersedes_event_id: str | None = None
    payload_reference: str | None = None
    source_metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def migrate_phase_one_content(cls, value: Any) -> Any:
        if isinstance(value, dict) and "content" in value:
            value = dict(value)
            content = value.pop("content")
            value.setdefault("raw_content", content)
            value.setdefault("display_content", content)
        return value

    @property
    def content(self) -> str:
        """Compatibility accessor; model prompts use resolved display text."""
        return self.display_content


class SummaryContent(BaseModel):
    """Provider-produced, evidence-only summary payload."""

    summary_text: str
    topics: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    thread_references: list[str] = Field(default_factory=list)
    artefact_references: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)


class Period(BaseModel):
    timeline_id: str
    scope_type: ScopeType = "channel"
    channel_id: str | None = None
    thread_id: str | None = None
    granularity: Granularity
    timezone: str
    local_start: datetime
    local_end: datetime
    utc_start: datetime
    utc_end: datetime

    @property
    def key(self) -> str:
        scope = f"{self.scope_type}:{self.channel_id or self.timeline_id}"
        if self.thread_id:
            scope += f":{self.thread_id}"
        return f"{scope}:{self.granularity}:{self.utc_start.isoformat()}"


class SummaryRecord(BaseModel):
    summary_id: str
    timeline_id: str
    scope_type: ScopeType = "channel"
    channel_id: str | None = None
    thread_id: str | None = None
    summary_reason: SummaryReason = "scheduled"
    granularity: Granularity
    timezone: str
    local_start: datetime
    local_end: datetime
    utc_start: datetime
    utc_end: datetime
    status: Literal["committed", "failed", "superseded"] = "committed"
    version: int
    source_watermark: str | None = None
    source_event_count: int
    source_event_ids: list[str] = Field(default_factory=list)
    child_summary_ids: list[str] = Field(default_factory=list)
    summary_text: str
    topics: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)
    participants: list[str] = Field(default_factory=list)
    thread_references: list[str] = Field(default_factory=list)
    artefact_references: list[str] = Field(default_factory=list)
    coverage_gaps: list[str] = Field(default_factory=list)
    embedded_thread_summaries: list[dict[str, Any]] = Field(default_factory=list)
    context_selections: dict[str, Any] = Field(default_factory=dict)
    model: str
    prompt_version: str
    generated_at: datetime
    source_hash: str
    model_config_hash: str
    retry_count: int = 0


class CoverageRecord(BaseModel):
    timeline_id: str
    scope_type: ScopeType = "channel"
    channel_id: str | None = None
    thread_id: str | None = None
    granularity: Granularity
    timezone: str
    local_start: datetime
    local_end: datetime
    utc_start: datetime
    utc_end: datetime
    source_event_count: int
    status: Literal["covered", "empty", "failed", "quarantined", "superseded"]
    partial_window: bool = False
    summary_id: str | None = None


class SafetySimulationResult(BaseModel):
    passed: bool
    assertions: list[dict[str, Any]]
    assembled_event_ids: list[str]


class AttachmentFinding(BaseModel):
    attachment_id: str
    content_hash: str | None = None
    mime_type: str | None = None
    source_event_id: str
    channel_id: str
    thread_id: str | None = None
    status: Literal["processed", "missing", "blocked", "unsupported", "failed"]
    description: str | None = None
    visible_text: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    model: str | None = None
    generated_at: datetime | None = None
    limitation: str | None = None


class ContextPackage(BaseModel):
    channel_id: str
    thread_id: str | None = None
    at: datetime
    previous_summaries: list[dict[str, Any]] = Field(default_factory=list)
    raw_unsummarized_tail: list[dict[str, Any]] = Field(default_factory=list)
    current_request: dict[str, Any]
    thread_history: list[dict[str, Any]] = Field(default_factory=list)
    then_channel: dict[str, Any] = Field(default_factory=dict)
    now_channel: dict[str, Any] = Field(default_factory=dict)
    related_threads: list[dict[str, Any]] = Field(default_factory=list)
    estimated_tokens: int

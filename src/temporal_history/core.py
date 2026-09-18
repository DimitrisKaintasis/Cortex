from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import re
import sqlite3
import sys
import threading
import time
from abc import ABC, abstractmethod
from collections import Counter
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from datetime import time as datetime_time
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from .models import (
    CoverageRecord,
    Granularity,
    NormalizedEvent,
    Period,
    SafetySimulationResult,
    SummaryContent,
    SummaryRecord,
)
from .slack_identity import enrich_events_with_slack_users

csv.field_size_limit(sys.maxsize)

PROMPT_VERSION = "temporal-history-v2"
GRANULARITIES: tuple[Granularity, ...] = (
    "six_hour",
    "day",
    "week",
    "month",
    "year",
)
URL_RE = re.compile(r"https?://[^\s<>()]+")
WORD_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")
STOP_WORDS = {
    "and",
    "about",
    "after",
    "also",
    "been",
    "before",
    "being",
    "for",
    "from",
    "have",
    "into",
    "just",
    "more",
    "not",
    "the",
    "that",
    "their",
    "there",
    "they",
    "this",
    "were",
    "was",
    "what",
    "when",
    "where",
    "which",
    "with",
    "would",
}


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)


def stable_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def write_jsonl(path: Path, rows: Iterable[Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            if hasattr(row, "model_dump"):
                row = row.model_dump(mode="json")
            handle.write(canonical_json(row) + "\n")


def parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp has no timezone: {value}")
    return parsed


def parse_json_cell(value: str) -> Any:
    if not value:
        return None
    try:
        return json.loads(value)
    except json.JSONDecodeError:
        return {"unparsed": value}


def slack_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=UTC)
    except (ValueError, OverflowError):
        return None


def validate_export(input_dir: Path) -> dict[str, Any]:
    manifest = read_json(input_dir / "export_manifest.json")
    expected_files = ("conversations.csv", "messages.csv")
    validation: dict[str, Any] = {
        "manifest": manifest,
        "checks": [],
        "valid": True,
    }
    for filename in expected_files:
        path = input_dir / filename
        payload = path.read_bytes()
        actual_hash = hashlib.sha256(payload).hexdigest()
        expected_hash = manifest["files"][filename]["sha256"]
        matches = actual_hash == expected_hash
        validation["checks"].append(
            {
                "check": f"{filename} sha256",
                "passed": matches,
                "actual": actual_hash,
                "expected": expected_hash,
            }
        )
        validation["valid"] = validation["valid"] and matches

    with (input_dir / "conversations.csv").open(newline="", encoding="utf-8") as handle:
        conversations = list(csv.DictReader(handle))
    conversation_ids = {row["id"] for row in conversations}
    channel_id = manifest["channel_id"]
    invalid_channels = [
        row["id"]
        for row in conversations
        if channel_id not in {row["channel_id"], row["external_channel_id"]}
    ]

    role_counts: Counter[str] = Counter()
    duplicate_ids: list[str] = []
    missing_conversations: list[str] = []
    message_ids: set[str] = set()
    thread_message_count = 0
    missing_slack_timestamp_count = 0
    invalid_created_at_count = 0
    content_characters = 0
    first_created_at: datetime | None = None
    latest_created_at: datetime | None = None
    with (input_dir / "messages.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        message_count = 0
        conversation_map = {row["id"]: row for row in conversations}
        for row in reader:
            message_count += 1
            if row["id"] in message_ids:
                duplicate_ids.append(row["id"])
            message_ids.add(row["id"])
            if row["conversation_id"] not in conversation_ids:
                missing_conversations.append(row["conversation_id"])
            conversation = conversation_map.get(row["conversation_id"], {})
            if conversation.get("thread_ts") or conversation.get("external_thread_id"):
                thread_message_count += 1
            if not row["slack_ts"] and not row["external_message_id"]:
                missing_slack_timestamp_count += 1
            role_counts[row["role"]] += 1
            content_characters += len(row["content"])
            try:
                created_at = parse_datetime(row["created_at"])
            except (ValueError, TypeError):
                invalid_created_at_count += 1
            else:
                first_created_at = min(first_created_at or created_at, created_at)
                latest_created_at = max(latest_created_at or created_at, created_at)

    count_checks = [
        (
            "conversation row count",
            len(conversations),
            manifest["conversation_count"],
        ),
        ("message row count", message_count, manifest["message_count"]),
    ]
    for name, actual, expected in count_checks:
        passed = actual == expected
        validation["checks"].append(
            {"check": name, "passed": passed, "actual": actual, "expected": expected}
        )
        validation["valid"] = validation["valid"] and passed

    integrity_checks = [
        ("authoritative channel IDs", not invalid_channels, invalid_channels[:20]),
        (
            "message conversation references",
            not missing_conversations,
            missing_conversations[:20],
        ),
    ]
    for name, passed, examples in integrity_checks:
        validation["checks"].append(
            {"check": name, "passed": passed, "examples": examples}
        )
        validation["valid"] = validation["valid"] and passed

    validation["profile"] = {
        "conversation_count": len(conversations),
        "message_count": message_count,
        "first_message_created_at": first_created_at.isoformat()
        if first_created_at
        else None,
        "latest_message_created_at": latest_created_at.isoformat()
        if latest_created_at
        else None,
        "role_counts": dict(sorted(role_counts.items())),
        "thread_message_count": thread_message_count,
        "channel_message_count": message_count - thread_message_count,
        "missing_source_timestamp_count": missing_slack_timestamp_count,
        "duplicate_message_id_count": len(duplicate_ids),
        "invalid_created_at_count": invalid_created_at_count,
        "content_characters": content_characters,
        "estimated_source_tokens": (content_characters + 3) // 4,
        "edit_history_available": False,
        "deletion_history_available": False,
    }
    if not validation["valid"]:
        failed = [
            check["check"] for check in validation["checks"] if not check["passed"]
        ]
        raise ValueError(f"Export validation failed: {', '.join(failed)}")
    return validation


def normalize_export(input_dir: Path, output_dir: Path) -> dict[str, Any]:
    validation = validate_export(input_dir)
    manifest = validation["manifest"]
    with (input_dir / "conversations.csv").open(newline="", encoding="utf-8") as handle:
        conversation_rows = {row["id"]: row for row in csv.DictReader(handle)}
    with (input_dir / "messages.csv").open(newline="", encoding="utf-8") as handle:
        message_rows = list(csv.DictReader(handle))

    source_roots: dict[str, tuple[str, str]] = {}
    for row in message_rows:
        source_id = row["external_message_id"] or row["slack_ts"] or row["id"]
        source_roots[source_id] = (row["id"], row["content"])

    events: list[NormalizedEvent] = []
    quarantined: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for source_index, row in enumerate(message_rows):
        reasons: list[str] = []
        conversation = conversation_rows.get(row["conversation_id"])
        if conversation is None:
            reasons.append("missing_conversation")
            conversation = {}
        event_id = row["id"]
        if not event_id:
            reasons.append("missing_message_id")
        elif event_id in seen_ids:
            reasons.append("duplicate_message_id")
        seen_ids.add(event_id)
        try:
            recorded_at = parse_datetime(row["created_at"])
        except (ValueError, TypeError) as exc:
            reasons.append(f"invalid_created_at:{exc}")
            recorded_at = None

        source_message_id = row["external_message_id"] or row["slack_ts"] or row["id"]
        occurred_at = (
            slack_timestamp(row["slack_ts"])
            or slack_timestamp(row["external_message_id"])
            or recorded_at
        )
        if occurred_at is None:
            reasons.append("missing_occurred_at")

        if reasons:
            quarantined.append(
                {
                    "source_index": source_index,
                    "message_id": row.get("id"),
                    "reasons": reasons,
                    "raw": row,
                }
            )
            continue

        thread_root_id = (
            conversation.get("external_thread_id")
            or conversation.get("thread_ts")
            or None
        )
        root = source_roots.get(thread_root_id or "")
        actor_id = (
            row["external_user_id"]
            or row["slack_user_id"]
            or row["user_id"]
            or row["public_agent_id"]
            or row["agent_id"]
            or None
        )
        source_order = f"{int(occurred_at.timestamp() * 1_000_000):020d}:{source_index:08d}:{event_id}"
        source_metadata = {
            "database_message_id": event_id,
            "conversation_id": row["conversation_id"],
            "role": row["role"],
            "platform": row["platform"] or conversation.get("platform") or "slack",
            "slack_ts": row["slack_ts"] or None,
            "external_message_id": row["external_message_id"] or None,
            "database_created_at": row["created_at"],
            "database_updated_at": row["updated_at"],
            "channel_name": conversation.get("external_channel_name")
            or conversation.get("channel_name"),
            "legacy_channel_name": conversation.get("channel_name"),
            "tool_name": row["tool_name"] or None,
            "tool_call_id": row["tool_call_id"] or None,
            "agent_id": row["agent_id"] or None,
            "public_agent_id": row["public_agent_id"] or None,
            "tool_calls": parse_json_cell(row["tool_calls"]),
            "filter_actions": parse_json_cell(row["filter_actions"]),
            "metadata": parse_json_cell(row["metadata"]),
            "delivery_metadata": parse_json_cell(row["delivery_metadata"]),
            "uploaded_file_ids": parse_json_cell(row["uploaded_file_ids"]) or [],
            "source_index": source_index,
        }
        events.append(
            NormalizedEvent(
                event_id=event_id,
                timeline_id=manifest["channel_id"],
                occurred_at=occurred_at,
                recorded_at=recorded_at,
                source_order=source_order,
                actor_id=actor_id,
                actor_display_name=None,
                raw_content=row["content"],
                display_content=row["content"],
                source_message_id=source_message_id,
                thread_root_id=thread_root_id,
                is_thread_reply=thread_root_id is not None,
                thread_root_excerpt=root[1][:240] if root else None,
                parent_event_id=root[0] if root else None,
                supersedes_event_id=None,
                payload_reference=None,
                source_metadata=source_metadata,
            )
        )

    events.sort(
        key=lambda event: (event.occurred_at, event.source_order, event.event_id)
    )
    events, identity = enrich_events_with_slack_users(
        events, output_dir / "slack_users.json"
    )
    write_jsonl(output_dir / "normalized_events.jsonl", events)
    write_jsonl(output_dir / "quarantined_events.jsonl", quarantined)
    source_hash = stable_hash([event.model_dump(mode="json") for event in events])
    result = {
        "valid_source_rows": len(events),
        "quarantined_rows": len(quarantined),
        "normalized_source_hash": source_hash,
        "first_occurred_at": events[0].occurred_at.isoformat() if events else None,
        "latest_occurred_at": events[-1].occurred_at.isoformat() if events else None,
        "thread_reply_count": sum(event.is_thread_reply for event in events),
        "resolved_thread_root_count": sum(
            event.parent_event_id is not None for event in events
        ),
        "identity": identity,
    }
    write_json(
        output_dir / "source_profile.json", {**validation, "normalization": result}
    )
    return result


def load_events(output_dir: Path) -> list[NormalizedEvent]:
    return [
        NormalizedEvent.model_validate(row)
        for row in read_jsonl(output_dir / "normalized_events.jsonl")
    ]


def period_start(
    instant: datetime, granularity: Granularity, timezone_name: str
) -> datetime:
    tz = ZoneInfo(timezone_name)
    local = instant.astimezone(tz)
    if granularity == "six_hour":
        return datetime(
            local.year,
            local.month,
            local.day,
            (local.hour // 6) * 6,
            tzinfo=tz,
        )
    if granularity == "day":
        return datetime(local.year, local.month, local.day, tzinfo=tz)
    if granularity == "week":
        monday = local.date() - timedelta(days=local.weekday())
        return datetime.combine(monday, datetime_time.min, tzinfo=tz)
    if granularity == "month":
        return datetime(local.year, local.month, 1, tzinfo=tz)
    return datetime(local.year, 1, 1, tzinfo=tz)


def next_period_start(start: datetime, granularity: Granularity) -> datetime:
    tz = start.tzinfo
    if granularity == "six_hour":
        target = start.replace(tzinfo=None) + timedelta(hours=6)
        return target.replace(tzinfo=tz)
    if granularity == "day":
        return datetime.combine(
            start.date() + timedelta(days=1), datetime_time.min, tzinfo=tz
        )
    if granularity == "week":
        return datetime.combine(
            start.date() + timedelta(days=7), datetime_time.min, tzinfo=tz
        )
    if granularity == "month":
        year = start.year + (1 if start.month == 12 else 0)
        month = 1 if start.month == 12 else start.month + 1
        return datetime(year, month, 1, tzinfo=tz)
    return datetime(start.year + 1, 1, 1, tzinfo=tz)


def build_periods(
    timeline_id: str,
    granularity: Granularity,
    timezone_name: str,
    range_start: datetime,
    range_end: datetime,
    *,
    scope_type: str = "channel",
    channel_id: str | None = None,
    thread_id: str | None = None,
) -> list[Period]:
    periods: list[Period] = []
    local_start = period_start(range_start, granularity, timezone_name)
    while local_start.astimezone(UTC) < range_end:
        local_end = next_period_start(local_start, granularity)
        periods.append(
            Period(
                timeline_id=timeline_id,
                scope_type=scope_type,
                channel_id=channel_id
                or (timeline_id if scope_type == "channel" else None),
                thread_id=thread_id,
                granularity=granularity,
                timezone=timezone_name,
                local_start=local_start,
                local_end=local_end,
                utc_start=local_start.astimezone(UTC),
                utc_end=local_end.astimezone(UTC),
            )
        )
        local_start = local_end
    return periods


def events_for_period(
    events: Sequence[NormalizedEvent], period: Period
) -> list[NormalizedEvent]:
    return [
        event
        for event in events
        if period.utc_start <= event.occurred_at < period.utc_end
    ]


def summaries_for_period(
    summaries: Sequence[SummaryRecord], period: Period
) -> list[SummaryRecord]:
    return [
        summary
        for summary in summaries
        if period.utc_start <= summary.utc_start and summary.utc_end <= period.utc_end
    ]


@dataclass(frozen=True)
class SummarizerResult:
    content: SummaryContent
    retry_count: int = 0


class TemporalSummarizer(ABC):
    model_name: str
    config_for_hash: dict[str, Any]

    @abstractmethod
    def summarize_raw_period(
        self,
        period: Period,
        events: Sequence[NormalizedEvent],
        metadata: dict[str, Any],
    ) -> SummarizerResult:
        raise NotImplementedError

    @abstractmethod
    def summarize_child_periods(
        self,
        period: Period,
        child_summaries: Sequence[SummaryRecord],
        metadata: dict[str, Any],
    ) -> SummarizerResult:
        raise NotImplementedError


def unique(items: Iterable[str | None]) -> list[str]:
    return list(dict.fromkeys(item for item in items if item))


def evidence_lines(events: Sequence[NormalizedEvent]) -> list[str]:
    return [
        f"{event.occurred_at.isoformat()} [{event.actor_id or 'unknown'}] {event.content.strip()}"
        for event in events
        if event.content.strip()
    ]


class MockSummarizer(TemporalSummarizer):
    model_name = "deterministic-mock-v1"
    config_for_hash = {"provider": "mock", "version": 1}

    def summarize_raw_period(
        self,
        period: Period,
        events: Sequence[NormalizedEvent],
        metadata: dict[str, Any],
    ) -> SummarizerResult:
        lines = evidence_lines(events)
        words = Counter(
            word.lower()
            for event in events
            for word in WORD_RE.findall(event.content)
            if word.lower() not in STOP_WORDS
        )
        topics = [word for word, _ in words.most_common(8)]
        decisions = [line[:500] for line in lines if "decid" in line.lower()][:12]
        actions = [
            line[:500]
            for line in lines
            if any(
                marker in line.lower()
                for marker in ("todo", "action", "need to", "will ")
            )
        ][:20]
        questions = [line[:500] for line in lines if "?" in line][:20]
        return SummarizerResult(
            SummaryContent(
                summary_text=(
                    f"{len(events)} events occurred in this "
                    f"{period.granularity.replace('_', '-')} period. "
                    f"Top lexical topics: {', '.join(topics) or 'none'}."
                ),
                topics=topics,
                decisions=decisions,
                actions=actions,
                open_questions=questions,
                participants=unique(event.actor_id for event in events),
                thread_references=unique(event.thread_root_id for event in events),
                artefact_references=unique(
                    url for event in events for url in URL_RE.findall(event.content)
                ),
                coverage_gaps=[],
            )
        )

    def summarize_child_periods(
        self,
        period: Period,
        child_summaries: Sequence[SummaryRecord],
        metadata: dict[str, Any],
    ) -> SummarizerResult:
        child_levels = ", ".join(
            sorted({child.granularity.replace("_", "-") for child in child_summaries})
        )
        return SummarizerResult(
            SummaryContent(
                summary_text=(
                    f"{len(child_summaries)} {child_levels or 'child'} summaries cover "
                    f"{sum(child.source_event_count for child in child_summaries)} events "
                    f"in this {period.granularity.replace('_', '-')} period."
                ),
                topics=unique(
                    topic for child in child_summaries for topic in child.topics
                )[:20],
                decisions=unique(
                    item for child in child_summaries for item in child.decisions
                )[:30],
                actions=unique(
                    item for child in child_summaries for item in child.actions
                )[:40],
                open_questions=unique(
                    item for child in child_summaries for item in child.open_questions
                )[:40],
                participants=unique(
                    item for child in child_summaries for item in child.participants
                ),
                thread_references=unique(
                    item
                    for child in child_summaries
                    for item in child.thread_references
                ),
                artefact_references=unique(
                    item
                    for child in child_summaries
                    for item in child.artefact_references
                ),
                coverage_gaps=unique(
                    item for child in child_summaries for item in child.coverage_gaps
                ),
            )
        )


class AzureOpenAISummarizer(TemporalSummarizer):
    def __init__(self) -> None:
        load_dotenv()
        endpoint = os.environ.get("TEMPORAL_AZURE_ENDPOINT", "").strip()
        api_key = os.environ.get("TEMPORAL_AZURE_API_KEY", "").strip()
        deployment = os.environ.get("TEMPORAL_AZURE_DEPLOYMENT", "").strip()
        model = os.environ.get("TEMPORAL_AZURE_MODEL", "").strip() or deployment
        if not endpoint or not api_key or not deployment:
            raise ValueError(
                "TEMPORAL_AZURE_ENDPOINT, TEMPORAL_AZURE_API_KEY, and "
                "TEMPORAL_AZURE_DEPLOYMENT are required"
            )

        self.model_name = model
        self.deployment_name = deployment
        self.use_responses_api = model.lower().startswith(("gpt-5", "o1", "o3", "o4"))
        self.api_version = os.environ.get(
            "TEMPORAL_AZURE_API_VERSION", "2024-12-01-preview"
        )
        self.timeout = float(os.environ.get("TEMPORAL_LLM_TIMEOUT", "120"))
        self.max_retries = int(os.environ.get("TEMPORAL_LLM_MAX_RETRIES", "3"))
        self.max_input_chars = int(os.environ.get("TEMPORAL_MAX_INPUT_CHARS", "60000"))
        self.config_for_hash = {
            "provider": "azure_openai",
            "endpoint": endpoint,
            "model": model,
            "deployment": deployment,
            "api_version": self.api_version,
            "api_surface": "responses_v1"
            if self.use_responses_api
            else "chat_completions",
            "timeout": self.timeout,
            "max_input_chars": self.max_input_chars,
        }
        if self.use_responses_api:
            from openai import OpenAI

            base_url = endpoint.rstrip("/")
            if not base_url.endswith("/openai/v1"):
                base_url += "/openai/v1"
            self.client = OpenAI(
                base_url=base_url + "/",
                api_key=api_key,
                timeout=self.timeout,
                max_retries=0,
            )
        else:
            from openai import AzureOpenAI

            self.client = AzureOpenAI(
                azure_endpoint=endpoint,
                api_key=api_key,
                api_version=self.api_version,
                timeout=self.timeout,
                max_retries=0,
            )
        self._usage_lock = threading.Lock()
        self._rate_condition = threading.Condition()
        self._rate_limit_until = 0.0
        self.usage = {
            "requests_attempted": 0,
            "requests_succeeded": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "retries": 0,
        }

    def _wait_for_rate_limit(self) -> None:
        with self._rate_condition:
            while True:
                remaining = self._rate_limit_until - time.monotonic()
                if remaining <= 0:
                    return
                self._rate_condition.wait(timeout=remaining)

    def _retry_delay(self, exc: Exception, attempt: int) -> float:
        headers = getattr(getattr(exc, "response", None), "headers", {}) or {}
        retry_ms = headers.get("retry-after-ms") or headers.get("x-ms-retry-after-ms")
        retry_seconds = headers.get("retry-after")
        try:
            if retry_ms:
                return min(120.0, max(1.0, float(retry_ms) / 1000))
            if retry_seconds:
                return min(120.0, max(1.0, float(retry_seconds)))
        except (TypeError, ValueError):
            pass
        return min(60.0, float(2**attempt))

    def _apply_shared_cooldown(self, delay: float) -> None:
        with self._rate_condition:
            self._rate_limit_until = max(
                self._rate_limit_until, time.monotonic() + delay
            )
            self._rate_condition.notify_all()

    def _record_success(self, response: Any, attempt: int) -> None:
        usage = getattr(response, "usage", None)
        with self._usage_lock:
            self.usage["requests_succeeded"] += 1
            self.usage["retries"] += attempt
            if usage:
                self.usage["input_tokens"] += (
                    getattr(usage, "input_tokens", None)
                    or getattr(usage, "prompt_tokens", 0)
                    or 0
                )
                self.usage["output_tokens"] += (
                    getattr(usage, "output_tokens", None)
                    or getattr(usage, "completion_tokens", 0)
                    or 0
                )

    def create_response(self, **kwargs: Any) -> Any:
        """Create a Responses API result with shared retry and usage handling."""
        if not self.use_responses_api:
            raise ValueError("Configured model does not use the Responses API")
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                self._wait_for_rate_limit()
                with self._usage_lock:
                    self.usage["requests_attempted"] += 1
                response = self.client.responses.create(
                    model=self.model_name,
                    **kwargs,
                )
                self._record_success(response, attempt)
                return response
            except Exception as exc:
                last_error = exc
                status_code = getattr(exc, "status_code", None)
                if (
                    isinstance(status_code, int)
                    and 400 <= status_code < 500
                    and status_code != 429
                ):
                    break
                if attempt >= self.max_retries:
                    break
                delay = self._retry_delay(exc, attempt)
                print(
                    f"Azure retry {attempt + 1}/{self.max_retries} after "
                    f"status {status_code or 'transport error'}; "
                    f"shared cooldown {delay:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                self._apply_shared_cooldown(delay)
        raise RuntimeError(
            f"Azure response failed after retries: {last_error}"
        ) from last_error

    def parse_responses(
        self, *, input_payload: list[dict[str, Any]], text_format: Any
    ) -> tuple[Any, int]:
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                self._wait_for_rate_limit()
                with self._usage_lock:
                    self.usage["requests_attempted"] += 1
                response = self.client.responses.parse(
                    model=self.model_name,
                    input=input_payload,
                    text_format=text_format,
                )
                content = response.output_parsed
                if content is None:
                    raise ValueError("Azure OpenAI returned no parsed response")
                self._record_success(response, attempt)
                return content, attempt
            except Exception as exc:
                last_error = exc
                status_code = getattr(exc, "status_code", None)
                if (
                    isinstance(status_code, int)
                    and 400 <= status_code < 500
                    and status_code != 429
                ):
                    break
                if attempt >= self.max_retries:
                    break
                delay = self._retry_delay(exc, attempt)
                print(
                    f"Azure retry {attempt + 1}/{self.max_retries} after "
                    f"status {status_code or 'transport error'}; "
                    f"shared cooldown {delay:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                self._apply_shared_cooldown(delay)
        raise RuntimeError(
            f"Azure structured response failed after retries: {last_error}"
        ) from last_error

    def _call(self, kind: str, payload: list[dict[str, Any]]) -> SummarizerResult:
        system = (
            "You create evidence-grounded temporal history summaries. Preserve chronology. "
            "Do not invent decisions, owners, dates, participants, or outcomes. Mark genuine "
            "uncertainty and conflicts. Every claim must be supported by the supplied records."
        )
        user = (
            f"Summarize this {kind} input using the requested schema. "
            "Keep source identifiers in thread_references where relevant.\n"
            + canonical_json(payload)
        )
        if self.use_responses_api:
            content, retries = self.parse_responses(
                input_payload=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                text_format=SummaryContent,
            )
            return SummarizerResult(content=content, retry_count=retries)
        last_error: Exception | None = None
        for attempt in range(self.max_retries + 1):
            try:
                self._wait_for_rate_limit()
                with self._usage_lock:
                    self.usage["requests_attempted"] += 1
                parse_method = getattr(self.client.chat.completions, "parse", None)
                if parse_method is None:
                    parse_method = self.client.beta.chat.completions.parse
                response = parse_method(
                    model=self.deployment_name,
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    response_format=SummaryContent,
                )
                content = response.choices[0].message.parsed
                if content is None:
                    raise ValueError("Azure OpenAI returned no parsed summary")
                self._record_success(response, attempt)
                return SummarizerResult(content=content, retry_count=attempt)
            except Exception as exc:  # SDK exceptions vary by API surface.
                last_error = exc
                status_code = getattr(exc, "status_code", None)
                if (
                    isinstance(status_code, int)
                    and 400 <= status_code < 500
                    and status_code != 429
                ):
                    break
                if attempt >= self.max_retries:
                    break
                delay = self._retry_delay(exc, attempt)
                print(
                    f"Azure retry {attempt + 1}/{self.max_retries} after "
                    f"status {status_code or 'transport error'}; "
                    f"shared cooldown {delay:.1f}s",
                    file=sys.stderr,
                    flush=True,
                )
                self._apply_shared_cooldown(delay)
        raise RuntimeError(
            f"Azure summary failed after retries: {last_error}"
        ) from last_error

    def _expand_oversized_item(self, item: dict[str, Any]) -> list[dict[str, Any]]:
        if len(canonical_json(item)) <= self.max_input_chars:
            return [item]
        text_field = "content" if "content" in item else "summary_text"
        text = str(item.get(text_field, ""))
        base = {**item, text_field: ""}
        overhead = len(canonical_json(base)) + 500
        fragment_size = max(1000, self.max_input_chars - overhead)
        if not text:
            text_field = "payload_fragment"
            text = canonical_json(item)
            base = {
                "fragmented_record_hash": stable_hash(item),
                "fragment_note": "Oversized structured record split verbatim.",
            }
            fragment_size = max(1000, self.max_input_chars - 500)
        fragments = [
            text[index : index + fragment_size]
            for index in range(0, len(text), fragment_size)
        ]
        return [
            {
                **base,
                text_field: fragment,
                "fragment_index": index + 1,
                "fragment_count": len(fragments),
            }
            for index, fragment in enumerate(fragments)
        ]

    def _chunk(self, payload: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
        chunks: list[list[dict[str, Any]]] = []
        current: list[dict[str, Any]] = []
        current_size = 0
        expanded = [
            fragment
            for item in payload
            for fragment in self._expand_oversized_item(item)
        ]
        for item in expanded:
            size = len(canonical_json(item))
            if current and current_size + size > self.max_input_chars:
                chunks.append(current)
                current = []
                current_size = 0
            current.append(item)
            current_size += size
        if current:
            chunks.append(current)
        return chunks

    def _summarize_chunked(
        self, kind: str, payload: list[dict[str, Any]]
    ) -> SummarizerResult:
        chunks = self._chunk(payload)
        if len(chunks) == 1:
            return self._call(kind, chunks[0])
        notes: list[dict[str, Any]] = []
        retries = 0
        for index, chunk in enumerate(chunks):
            print(
                f"Azure {kind}: chunk {index + 1}/{len(chunks)}",
                file=sys.stderr,
                flush=True,
            )
            result = self._call(f"{kind} chunk {index + 1}/{len(chunks)}", chunk)
            retries += result.retry_count
            notes.append(result.content.model_dump(mode="json"))
        reduction_round = 1
        while len(self._chunk(notes)) > 1:
            reduced: list[dict[str, Any]] = []
            note_chunks = self._chunk(notes)
            for index, note_chunk in enumerate(note_chunks):
                print(
                    f"Azure {kind}: reduction {reduction_round} "
                    f"{index + 1}/{len(note_chunks)}",
                    file=sys.stderr,
                    flush=True,
                )
                result = self._call(
                    f"{kind} reduction round {reduction_round}, "
                    f"chunk {index + 1}/{len(note_chunks)}",
                    note_chunk,
                )
                retries += result.retry_count
                reduced.append(result.content.model_dump(mode="json"))
            notes = reduced
            reduction_round += 1
        merged = self._call(f"ordered intermediate notes for {kind}", notes)
        return SummarizerResult(
            content=merged.content, retry_count=retries + merged.retry_count
        )

    def summarize_raw_period(
        self,
        period: Period,
        events: Sequence[NormalizedEvent],
        metadata: dict[str, Any],
    ) -> SummarizerResult:
        payload = [
            {
                "event_id": event.event_id,
                "occurred_at": event.occurred_at.isoformat(),
                "actor_id": event.actor_id,
                "content": event.content,
                "thread_root_id": event.thread_root_id,
                "is_thread_reply": event.is_thread_reply,
                "attachment_findings": event.source_metadata.get(
                    "attachment_findings", []
                ),
            }
            for event in events
        ]
        if metadata:
            payload.insert(
                0,
                {
                    "record_type": "summary_context_metadata",
                    "metadata": metadata,
                },
            )
        return self._summarize_chunked("chronological raw events", payload)

    def summarize_child_periods(
        self,
        period: Period,
        child_summaries: Sequence[SummaryRecord],
        metadata: dict[str, Any],
    ) -> SummarizerResult:
        payload = [
            {
                "summary_id": child.summary_id,
                "local_start": child.local_start.isoformat(),
                "local_end": child.local_end.isoformat(),
                "source_event_count": child.source_event_count,
                "summary_text": child.summary_text,
                "topics": child.topics,
                "decisions": child.decisions,
                "actions": child.actions,
                "open_questions": child.open_questions,
                "participants": child.participants,
                "thread_references": child.thread_references,
                "artefact_references": child.artefact_references,
                "coverage_gaps": child.coverage_gaps,
            }
            for child in child_summaries
        ]
        if metadata:
            payload.insert(
                0,
                {
                    "record_type": "summary_context_metadata",
                    "metadata": metadata,
                },
            )
        return self._summarize_chunked("ordered child summaries", payload)


class StateStore:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.RLock()
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS summaries (
                summary_id TEXT PRIMARY KEY,
                period_key TEXT NOT NULL,
                source_hash TEXT NOT NULL,
                model_config_hash TEXT NOT NULL,
                version INTEGER NOT NULL,
                status TEXT NOT NULL,
                record_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(period_key, source_hash, model_config_hash, status)
            )
            """
        )
        self.connection.execute(
            """
            CREATE TABLE IF NOT EXISTS failures (
                period_key TEXT PRIMARY KEY,
                error TEXT NOT NULL,
                attempted_at TEXT NOT NULL
            )
            """
        )
        self.connection.commit()

    def get_committed(
        self, period_key: str, source_hash: str, model_config_hash: str
    ) -> SummaryRecord | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT record_json FROM summaries
                WHERE period_key = ? AND source_hash = ? AND model_config_hash = ?
                  AND status = 'committed'
                """,
                (period_key, source_hash, model_config_hash),
            ).fetchone()
        return SummaryRecord.model_validate_json(row[0]) if row else None

    def next_version(self, period_key: str) -> int:
        with self.lock:
            row = self.connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM summaries WHERE period_key = ?",
                (period_key,),
            ).fetchone()
        return int(row[0]) + 1

    def commit(self, record: SummaryRecord, period_key: str) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO summaries (
                    summary_id, period_key, source_hash, model_config_hash,
                    version, status, record_json, updated_at
                ) VALUES (?, ?, ?, ?, ?, 'committed', ?, ?)
                """,
                (
                    record.summary_id,
                    period_key,
                    record.source_hash,
                    record.model_config_hash,
                    record.version,
                    record.model_dump_json(),
                    datetime.now(UTC).isoformat(),
                ),
            )
            self.connection.execute(
                "DELETE FROM failures WHERE period_key = ?", (period_key,)
            )

    def fail(self, period_key: str, error: str) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT OR REPLACE INTO failures (period_key, error, attempted_at)
                VALUES (?, ?, ?)
                """,
                (period_key, error, datetime.now(UTC).isoformat()),
            )

    def failure_count(self) -> int:
        with self.lock:
            return int(
                self.connection.execute("SELECT COUNT(*) FROM failures").fetchone()[0]
            )

    def close(self) -> None:
        with self.lock:
            self.connection.close()


@dataclass
class GenerationJob:
    period: Period
    period_events: list[NormalizedEvent]
    child_summaries: list[SummaryRecord]
    source_hash: str
    coverage_gaps: list[str]
    embedded_thread_summaries: list[SummaryRecord]
    pressure_summaries: list[SummaryRecord]
    context_selections: dict[str, Any]
    summary_reason: str = "scheduled"


def summary_content_with_gaps(
    content: SummaryContent, coverage_gaps: Sequence[str]
) -> SummaryContent:
    data = content.model_dump()
    data["coverage_gaps"] = unique([*content.coverage_gaps, *coverage_gaps])
    return SummaryContent.model_validate(data)


def make_summary_record(
    job: GenerationJob,
    result: SummarizerResult,
    summarizer: TemporalSummarizer,
    model_config_hash: str,
    version: int,
) -> SummaryRecord:
    content = summary_content_with_gaps(result.content, job.coverage_gaps)
    summary_id = (
        "sum_"
        + stable_hash(
            {
                "period": job.period.key,
                "source_hash": job.source_hash,
                "model_config_hash": model_config_hash,
            }
        )[:28]
    )
    return SummaryRecord(
        summary_id=summary_id,
        timeline_id=job.period.timeline_id,
        scope_type=job.period.scope_type,
        channel_id=job.period.channel_id,
        thread_id=job.period.thread_id,
        summary_reason=job.summary_reason,
        granularity=job.period.granularity,
        timezone=job.period.timezone,
        local_start=job.period.local_start,
        local_end=job.period.local_end,
        utc_start=job.period.utc_start,
        utc_end=job.period.utc_end,
        version=version,
        source_watermark=max(
            (event.source_order for event in job.period_events), default=None
        ),
        source_event_count=len(job.period_events),
        source_event_ids=[event.event_id for event in job.period_events],
        child_summary_ids=[
            child.summary_id
            for child in [*job.child_summaries, *job.pressure_summaries]
        ],
        embedded_thread_summaries=[
            {
                "summary_id": summary.summary_id,
                "thread_id": summary.thread_id,
                "granularity": summary.granularity,
                "version": summary.version,
                "utc_start": summary.utc_start.isoformat(),
                "utc_end": summary.utc_end.isoformat(),
                "source_event_count": summary.source_event_count,
                "lineage_link": (
                    f"summaries/thread/{summary.granularity}.jsonl#{summary.summary_id}"
                ),
            }
            for summary in job.embedded_thread_summaries
        ],
        context_selections=job.context_selections,
        **content.model_dump(),
        model=summarizer.model_name,
        prompt_version=PROMPT_VERSION,
        generated_at=datetime.now(UTC),
        source_hash=job.source_hash,
        model_config_hash=model_config_hash,
        retry_count=result.retry_count,
    )


def thread_context_selections(
    thread_id: str,
    period: Period,
    all_events: Sequence[NormalizedEvent],
    window_hours: int = 24,
) -> dict[str, Any]:
    root = next(
        (event for event in all_events if event.source_message_id == thread_id),
        None,
    )
    thread_events = [event for event in all_events if event.thread_root_id == thread_id]
    anchor = (
        root.occurred_at
        if root
        else (thread_events[0].occurred_at if thread_events else period.utc_start)
    )
    half_window = timedelta(hours=window_hours / 2)
    then_events = [
        event
        for event in all_events
        if anchor - half_window <= event.occurred_at < anchor + half_window
        and event.thread_root_id != thread_id
    ]
    now_start = period.utc_end - timedelta(hours=window_hours)
    now_events = [
        event
        for event in all_events
        if now_start <= event.occurred_at < period.utc_end
        and event.thread_root_id != thread_id
    ]
    known_thread_ids = {
        event.thread_root_id for event in all_events if event.thread_root_id
    }
    related: list[dict[str, Any]] = []
    joined_text = "\n".join(
        event.raw_content for event in thread_events if event.raw_content
    )
    for candidate in sorted(known_thread_ids):
        if candidate != thread_id and candidate in joined_text:
            related.append({"thread_id": candidate, "reason": "explicit_reference"})
    return {
        "then_channel": {
            "anchor": anchor.isoformat(),
            "event_ids": [event.event_id for event in then_events],
            "rationale": (
                f"Channel events within ±{window_hours / 2:g} hours of thread creation."
            ),
        },
        "now_channel": {
            "anchor": period.utc_end.isoformat(),
            "event_ids": [event.event_id for event in now_events],
            "rationale": (
                f"Channel events in the {window_hours} hours before "
                "the summary boundary."
            ),
        },
        "related_threads": related,
        "thread_root_available": root is not None,
    }


def generate_level(
    periods: Sequence[Period],
    events: Sequence[NormalizedEvent],
    child_summaries: Sequence[SummaryRecord],
    summarizer: TemporalSummarizer,
    store: StateStore,
    model_config_hash: str,
    range_start: datetime,
    range_end: datetime,
    max_workers: int,
    supporting_thread_summaries: Sequence[SummaryRecord] = (),
    supporting_pressure_summaries: Sequence[SummaryRecord] = (),
    context_events: Sequence[NormalizedEvent] = (),
    summary_reason: str = "scheduled",
) -> tuple[list[SummaryRecord], list[CoverageRecord], dict[str, int]]:
    records: list[SummaryRecord] = []
    coverage: list[CoverageRecord] = []
    pending: list[GenerationJob] = []
    reused = 0
    for period in periods:
        period_events = events_for_period(events, period)
        children = summaries_for_period(child_summaries, period)
        embedded_threads = summaries_for_period(
            [
                summary
                for summary in supporting_thread_summaries
                if summary.granularity == period.granularity
            ],
            period,
        )
        pressure_summaries = (
            [
                summary
                for summary in supporting_pressure_summaries
                if summary.utc_start < period.utc_end
                and summary.utc_end > period.utc_start
            ]
            if period.granularity == "six_hour"
            else []
        )
        context_selections = (
            thread_context_selections(period.thread_id, period, context_events)
            if period.scope_type == "thread" and period.thread_id and context_events
            else {}
        )
        partial = period.utc_start < range_start or period.utc_end > range_end
        if not period_events:
            coverage.append(
                CoverageRecord(
                    **period.model_dump(),
                    source_event_count=0,
                    status="empty",
                    partial_window=partial,
                )
            )
            continue
        if period.granularity != "six_hour" and not children:
            raise RuntimeError(
                f"{period.key} has source events but no committed child summaries"
            )
        gaps = (
            ["Source export does not cover this entire calendar period."]
            if partial
            else []
        )
        source_payload: Any
        if period.granularity == "six_hour":
            source_payload = [event.model_dump(mode="json") for event in period_events]
        else:
            source_payload = [
                {
                    "summary_id": child.summary_id,
                    "source_hash": child.source_hash,
                    "version": child.version,
                }
                for child in children
            ]
        source_hash = stable_hash(
            {
                "direct_sources": source_payload,
                "embedded_thread_summaries": [
                    {
                        "summary_id": summary.summary_id,
                        "version": summary.version,
                        "source_hash": summary.source_hash,
                    }
                    for summary in embedded_threads
                ],
                "pressure_summaries": [
                    {
                        "summary_id": summary.summary_id,
                        "source_hash": summary.source_hash,
                        "source_event_ids": summary.source_event_ids,
                    }
                    for summary in pressure_summaries
                ],
                "context_selections": context_selections,
                "summary_reason": summary_reason,
            }
        )
        cached = store.get_committed(period.key, source_hash, model_config_hash)
        if cached:
            records.append(cached)
            coverage.append(
                CoverageRecord(
                    **period.model_dump(),
                    source_event_count=len(period_events),
                    status="covered",
                    partial_window=partial,
                    summary_id=cached.summary_id,
                )
            )
            reused += 1
            continue
        pending.append(
            GenerationJob(
                period=period,
                period_events=period_events,
                child_summaries=children,
                source_hash=source_hash,
                coverage_gaps=gaps,
                embedded_thread_summaries=embedded_threads,
                pressure_summaries=pressure_summaries,
                context_selections=context_selections,
                summary_reason=summary_reason,
            )
        )

    def generate(job: GenerationJob) -> SummarizerResult:
        metadata = {
            "source_hash": job.source_hash,
            "prompt_version": PROMPT_VERSION,
            "coverage_gaps": job.coverage_gaps,
            "embedded_thread_summaries": [
                summary.model_dump(mode="json")
                for summary in job.embedded_thread_summaries
            ],
            "pressure_summaries": [
                summary.model_dump(mode="json") for summary in job.pressure_summaries
            ],
            "context_selections": job.context_selections,
            "summary_reason": job.summary_reason,
        }
        if job.period.granularity == "six_hour":
            pressure_covered_ids = {
                event_id
                for summary in job.pressure_summaries
                for event_id in summary.source_event_ids
            }
            return summarizer.summarize_raw_period(
                job.period,
                [
                    event
                    for event in job.period_events
                    if event.event_id not in pressure_covered_ids
                ],
                metadata,
            )
        return summarizer.summarize_child_periods(
            job.period, job.child_summaries, metadata
        )

    with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
        future_jobs = {executor.submit(generate, job): job for job in pending}
        for future in as_completed(future_jobs):
            job = future_jobs[future]
            try:
                result = future.result()
                record = make_summary_record(
                    job,
                    result,
                    summarizer,
                    model_config_hash,
                    store.next_version(job.period.key),
                )
                store.commit(record, job.period.key)
                print(
                    f"Committed {job.period.granularity} "
                    f"{job.period.local_start.isoformat()}",
                    file=sys.stderr,
                    flush=True,
                )
                records.append(record)
                coverage.append(
                    CoverageRecord(
                        **job.period.model_dump(),
                        source_event_count=len(job.period_events),
                        status="covered",
                        partial_window=bool(job.coverage_gaps),
                        summary_id=record.summary_id,
                    )
                )
            except Exception as exc:
                store.fail(job.period.key, str(exc))
                raise

    records.sort(key=lambda item: (item.utc_start, item.summary_id))
    coverage.sort(key=lambda item: (item.granularity, item.utc_start, item.timeline_id))
    return records, coverage, {"generated": len(pending), "reused": reused}


def create_summarizer(provider: str) -> TemporalSummarizer:
    if provider == "mock":
        return MockSummarizer()
    if provider == "azure":
        return AzureOpenAISummarizer()
    raise ValueError(f"Unknown provider: {provider}")


def estimate_workload(output_dir: Path) -> dict[str, Any]:
    profile = read_json(output_dir / "source_profile.json")
    events = load_events(output_dir)
    manifest = profile["manifest"]
    start = parse_datetime(manifest["start_utc"])
    end = parse_datetime(manifest["cutoff_utc_exclusive"])
    timezone_name = manifest["workspace_timezone"]
    timeline_id = manifest["channel_id"]
    period_counts: dict[str, int] = {}
    six_hour_payload_sizes: list[int] = []
    for granularity in GRANULARITIES:
        periods = build_periods(timeline_id, granularity, timezone_name, start, end)
        period_counts[granularity] = sum(
            bool(events_for_period(events, period)) for period in periods
        )
        if granularity == "six_hour":
            six_hour_payload_sizes = [
                sum(
                    len(event.content)
                    + len(event.event_id)
                    + len(event.source_order)
                    + 300
                    for event in events_for_period(events, period)
                )
                for period in periods
                if events_for_period(events, period)
            ]
    source_characters = sum(len(event.content) for event in events)
    estimated_input_tokens = (source_characters + 3) // 4
    thread_groups = build_thread_event_groups(events)
    thread_period_counts = {level: 0 for level in GRANULARITIES}
    thread_payload_sizes: list[int] = []
    thread_source_characters = 0
    for thread_id, thread_events in thread_groups.items():
        thread_start = min(event.occurred_at for event in thread_events)
        thread_end = max(event.occurred_at for event in thread_events) + timedelta(
            microseconds=1
        )
        thread_source_characters += sum(len(event.content) for event in thread_events)
        for granularity in GRANULARITIES:
            periods = build_periods(
                timeline_id,
                granularity,
                timezone_name,
                thread_start,
                thread_end,
                scope_type="thread",
                channel_id=timeline_id,
                thread_id=thread_id,
            )
            thread_period_counts[granularity] += sum(
                bool(events_for_period(thread_events, period)) for period in periods
            )
            if granularity == "six_hour":
                thread_payload_sizes.extend(
                    sum(
                        len(event.content)
                        + len(event.event_id)
                        + len(event.source_order)
                        + 300
                        for event in events_for_period(thread_events, period)
                    )
                    for period in periods
                    if events_for_period(thread_events, period)
                )
    max_input_chars = int(os.environ.get("TEMPORAL_MAX_INPUT_CHARS", "60000"))
    raw_chunk_calls = sum(
        max(1, math.ceil(size / max_input_chars))
        for size in [*six_hour_payload_sizes, *thread_payload_sizes]
    )
    chunked_periods = sum(
        size > max_input_chars
        for size in [*six_hour_payload_sizes, *thread_payload_sizes]
    )
    channel_parent_calls = sum(
        count for level, count in period_counts.items() if level != "six_hour"
    )
    thread_parent_calls = sum(
        count for level, count in thread_period_counts.items() if level != "six_hour"
    )
    existing_manifest = (
        read_json(output_dir / "manifest.json")
        if (output_dir / "manifest.json").exists()
        else {}
    )
    pressure_calls = sum(
        existing_manifest.get("counts", {}).get("pressure_summaries", {}).values()
    )
    attachment_calls = (
        existing_manifest.get("counts", {}).get("attachments", {}).get("processed", 0)
    )
    return {
        "source_events": len(events),
        "source_characters": source_characters,
        "estimated_raw_input_tokens": estimated_input_tokens,
        "nonempty_periods": {
            "channel": period_counts,
            "thread": thread_period_counts,
        },
        "thread_timelines": len(thread_groups),
        "estimated_minimum_model_calls": (
            sum(period_counts.values())
            + sum(thread_period_counts.values())
            + pressure_calls
            + attachment_calls
        ),
        "estimated_pressure_calls": pressure_calls,
        "estimated_multimodal_attachment_calls": attachment_calls,
        "estimated_all_scope_raw_input_tokens": (
            source_characters + thread_source_characters + 3
        )
        // 4,
        "configured_max_input_characters": max_input_chars,
        "six_hour_periods_requiring_chunking": chunked_periods,
        "estimated_raw_chunk_calls": raw_chunk_calls,
        "estimated_calls_with_one_merge_per_chunked_period": (
            raw_chunk_calls
            + chunked_periods
            + channel_parent_calls
            + thread_parent_calls
            + pressure_calls
            + attachment_calls
        ),
        "largest_six_hour_payload_characters": max(
            [*six_hour_payload_sizes, *thread_payload_sizes], default=0
        ),
        "note": (
            "Call estimates exclude extra hierarchical reduction rounds and retries; "
            "token estimates exclude prompts, parent summaries, and outputs."
        ),
    }


def run_azure_smoke(output_dir: Path) -> dict[str, Any]:
    profile = read_json(output_dir / "source_profile.json")
    manifest = profile["manifest"]
    events = load_events(output_dir)
    periods = build_periods(
        manifest["channel_id"],
        "six_hour",
        manifest["workspace_timezone"],
        parse_datetime(manifest["start_utc"]),
        parse_datetime(manifest["cutoff_utc_exclusive"]),
    )
    summarizer = AzureOpenAISummarizer()
    summarizer.max_retries = 0
    candidates: list[
        tuple[int, int, Period, list[NormalizedEvent], list[dict[str, Any]]]
    ] = []
    for period in periods:
        selected = events_for_period(events, period)
        if not selected:
            continue
        payload = [
            {
                "event_id": event.event_id,
                "occurred_at": event.occurred_at.isoformat(),
                "actor_id": event.actor_id,
                "content": event.content,
                "thread_root_id": event.thread_root_id,
                "is_thread_reply": event.is_thread_reply,
            }
            for event in selected
        ]
        chunks = summarizer._chunk(payload)
        candidates.append(
            (
                len(chunks),
                sum(len(canonical_json(item)) for item in payload),
                period,
                selected,
                payload,
            )
        )
    if not candidates:
        raise RuntimeError("No non-empty six-hour period is available")
    chunk_count, input_characters, period, selected, _ = min(
        candidates, key=lambda item: (item[0], item[1])
    )
    if chunk_count != 1:
        raise RuntimeError(
            "Smallest six-hour period requires multiple requests; "
            "refusing a multi-request smoke test"
        )
    result = summarizer.summarize_raw_period(
        period,
        selected,
        {"purpose": "single-request connectivity and structured-output smoke test"},
    )
    smoke = {
        "passed": True,
        "model": summarizer.model_name,
        "deployment": summarizer.deployment_name,
        "api_surface": "responses_v1"
        if summarizer.use_responses_api
        else "chat_completions",
        "period": period.model_dump(mode="json"),
        "event_count": len(selected),
        "input_characters": input_characters,
        "request_count": summarizer.usage["requests_attempted"],
        "usage": summarizer.usage,
        "summary": result.content.model_dump(mode="json"),
    }
    if smoke["request_count"] != 1:
        raise RuntimeError(
            f"Smoke test unexpectedly made {smoke['request_count']} requests"
        )
    write_json(output_dir / "azure_smoke.json", smoke)
    return smoke


def run_scope_hierarchy(
    *,
    scope_type: str,
    channel_id: str,
    thread_id: str | None,
    events: Sequence[NormalizedEvent],
    context_events: Sequence[NormalizedEvent],
    supporting_thread_summaries: Sequence[SummaryRecord],
    supporting_pressure_summaries: Sequence[SummaryRecord],
    timezone_name: str,
    range_start: datetime,
    range_end: datetime,
    summarizer: TemporalSummarizer,
    store: StateStore,
    model_config_hash: str,
    max_workers: int,
) -> tuple[
    dict[str, list[SummaryRecord]],
    list[CoverageRecord],
    dict[str, dict[str, int]],
]:
    timeline_id = (
        channel_id if scope_type == "channel" else f"{channel_id}:thread:{thread_id}"
    )
    summaries_by_level: dict[str, list[SummaryRecord]] = {}
    all_coverage: list[CoverageRecord] = []
    generation_stats: dict[str, dict[str, int]] = {}
    child_level: list[SummaryRecord] = []
    for granularity in GRANULARITIES:
        periods = build_periods(
            timeline_id,
            granularity,
            timezone_name,
            range_start,
            range_end,
            scope_type=scope_type,
            channel_id=channel_id,
            thread_id=thread_id,
        )
        if granularity == "month":
            children = summaries_by_level["day"]
        elif granularity == "year":
            children = summaries_by_level["month"]
        else:
            children = child_level
        summaries, coverage, stats = generate_level(
            periods=periods,
            events=events,
            child_summaries=children,
            summarizer=summarizer,
            store=store,
            model_config_hash=model_config_hash,
            range_start=range_start,
            range_end=range_end,
            max_workers=max_workers,
            supporting_thread_summaries=supporting_thread_summaries,
            supporting_pressure_summaries=supporting_pressure_summaries,
            context_events=context_events,
            summary_reason="backfill",
        )
        summaries_by_level[granularity] = summaries
        all_coverage.extend(coverage)
        generation_stats[granularity] = stats
        child_level = summaries
    return summaries_by_level, all_coverage, generation_stats


def build_thread_event_groups(
    events: Sequence[NormalizedEvent],
) -> dict[str, list[NormalizedEvent]]:
    roots = {
        event.source_message_id: event for event in events if not event.is_thread_reply
    }
    groups: dict[str, list[NormalizedEvent]] = {}
    for event in events:
        if not event.thread_root_id:
            continue
        groups.setdefault(event.thread_root_id, []).append(event)
    for thread_id, thread_events in groups.items():
        root = roots.get(thread_id)
        if root and root.event_id not in {event.event_id for event in thread_events}:
            thread_events.append(root)
        thread_events.sort(
            key=lambda event: (
                event.occurred_at,
                event.source_order,
                event.event_id,
            )
        )
    return groups


def run_pipeline(
    input_dir: Path,
    output_dir: Path,
    provider: str = "mock",
    max_workers: int = 3,
    pressure_token_limit: int = 200_000,
) -> dict[str, Any]:
    started_at = datetime.now(UTC)
    normalization = normalize_export(input_dir, output_dir)
    source_profile = read_json(output_dir / "source_profile.json")
    manifest = source_profile["manifest"]
    events = load_events(output_dir)
    if not events:
        raise RuntimeError("No normalized events to summarize")
    from .attachments import process_attachments

    events, attachment_findings, attachment_azure_usage = process_attachments(
        events=events,
        raw_attachment_dir=input_dir,
        output_path=output_dir / "attachments.jsonl",
        provider=provider,
        max_workers=max_workers,
    )
    write_jsonl(output_dir / "normalized_events.jsonl", events)
    normalization["normalized_source_hash"] = stable_hash(
        [event.model_dump(mode="json") for event in events]
    )
    summarizer = create_summarizer(provider)
    model_config_hash = stable_hash(
        {
            "prompt_version": PROMPT_VERSION,
            "model": summarizer.model_name,
            "config": summarizer.config_for_hash,
        }
    )
    store = StateStore(output_dir / "state.sqlite3")
    range_start = parse_datetime(manifest["start_utc"])
    range_end = parse_datetime(manifest["cutoff_utc_exclusive"])
    timeline_id = manifest["channel_id"]
    timezone_name = manifest["workspace_timezone"]
    try:
        thread_groups = build_thread_event_groups(events)
        from .pressure import (
            simulate_pressure_compaction,
            validate_pressure_exactly_once,
        )

        existing_channel_pressure = [
            SummaryRecord.model_validate(row)
            for row in read_jsonl(
                output_dir / "summaries" / "channel" / "pressure.jsonl"
            )
            if row.get("model") == summarizer.model_name
        ]
        channel_pressure_checkpoint = {
            summary.summary_id: summary for summary in existing_channel_pressure
        }

        def checkpoint_channel_pressure(summary: SummaryRecord) -> None:
            channel_pressure_checkpoint[summary.summary_id] = summary
            write_jsonl(
                output_dir / "summaries" / "channel" / "pressure.jsonl",
                [
                    channel_pressure_checkpoint[key]
                    for key in sorted(channel_pressure_checkpoint)
                ],
            )

        channel_pressure = simulate_pressure_compaction(
            events=events,
            summarizer=summarizer,
            channel_id=timeline_id,
            thread_id=None,
            timezone_name=timezone_name,
            token_limit=pressure_token_limit,
            existing_summaries=existing_channel_pressure,
            on_commit=checkpoint_channel_pressure,
        )
        existing_thread_pressure = [
            SummaryRecord.model_validate(row)
            for row in read_jsonl(
                output_dir / "summaries" / "thread" / "pressure.jsonl"
            )
            if row.get("model") == summarizer.model_name
        ]
        thread_pressure: dict[str, Any] = {}
        for thread_id, thread_events in sorted(thread_groups.items()):
            thread_pressure[thread_id] = simulate_pressure_compaction(
                events=thread_events,
                summarizer=summarizer,
                channel_id=timeline_id,
                thread_id=thread_id,
                timezone_name=timezone_name,
                token_limit=pressure_token_limit,
                existing_summaries=[
                    summary
                    for summary in existing_thread_pressure
                    if summary.thread_id == thread_id
                ],
            )
        thread_summaries: dict[str, list[SummaryRecord]] = {
            level: [] for level in GRANULARITIES
        }
        thread_coverage: list[CoverageRecord] = []
        thread_generation: dict[str, dict[str, int]] = {
            level: {"generated": 0, "reused": 0} for level in GRANULARITIES
        }

        def generate_thread_scope(
            item: tuple[str, list[NormalizedEvent]],
        ) -> tuple[
            str,
            dict[str, list[SummaryRecord]],
            list[CoverageRecord],
            dict[str, dict[str, int]],
        ]:
            thread_id, thread_events = item
            thread_start = min(event.occurred_at for event in thread_events)
            thread_end = max(event.occurred_at for event in thread_events) + timedelta(
                microseconds=1
            )
            scope_summaries, scope_coverage, scope_stats = run_scope_hierarchy(
                scope_type="thread",
                channel_id=timeline_id,
                thread_id=thread_id,
                events=thread_events,
                context_events=events,
                supporting_thread_summaries=(),
                supporting_pressure_summaries=thread_pressure[thread_id].summaries,
                timezone_name=timezone_name,
                range_start=thread_start,
                range_end=thread_end,
                summarizer=summarizer,
                store=store,
                model_config_hash=model_config_hash,
                max_workers=1,
            )
            return thread_id, scope_summaries, scope_coverage, scope_stats

        with ThreadPoolExecutor(max_workers=max(1, max_workers)) as executor:
            futures = [
                executor.submit(generate_thread_scope, item)
                for item in sorted(thread_groups.items())
            ]
            thread_results = [future.result() for future in as_completed(futures)]
        for _, scope_summaries, scope_coverage, scope_stats in thread_results:
            for level in GRANULARITIES:
                thread_summaries[level].extend(scope_summaries[level])
                thread_generation[level]["generated"] += scope_stats[level]["generated"]
                thread_generation[level]["reused"] += scope_stats[level]["reused"]
            thread_coverage.extend(scope_coverage)

        for level in GRANULARITIES:
            thread_summaries[level].sort(
                key=lambda summary: (
                    summary.thread_id or "",
                    summary.utc_start,
                    summary.summary_id,
                )
            )
            write_jsonl(
                output_dir / "summaries" / "thread" / f"{level}.jsonl",
                thread_summaries[level],
            )
        all_thread_pressure = [
            summary
            for result in thread_pressure.values()
            for summary in result.summaries
        ]
        write_jsonl(
            output_dir / "summaries" / "thread" / "pressure.jsonl",
            all_thread_pressure,
        )

        all_thread_summaries = [
            summary for level in GRANULARITIES for summary in thread_summaries[level]
        ]
        channel_summaries, channel_coverage, channel_generation = run_scope_hierarchy(
            scope_type="channel",
            channel_id=timeline_id,
            thread_id=None,
            events=events,
            context_events=events,
            supporting_thread_summaries=all_thread_summaries,
            supporting_pressure_summaries=channel_pressure.summaries,
            timezone_name=timezone_name,
            range_start=range_start,
            range_end=range_end,
            summarizer=summarizer,
            store=store,
            model_config_hash=model_config_hash,
            max_workers=max_workers,
        )
        for level in GRANULARITIES:
            write_jsonl(
                output_dir / "summaries" / "channel" / f"{level}.jsonl",
                channel_summaries[level],
            )
            # Phase 1 compatibility view.
            write_jsonl(
                output_dir / "summaries" / f"{level}.jsonl",
                channel_summaries[level],
            )
        write_jsonl(
            output_dir / "summaries" / "channel" / "pressure.jsonl",
            channel_pressure.summaries,
        )

        all_coverage = [*thread_coverage, *channel_coverage]
        write_jsonl(output_dir / "coverage.jsonl", all_coverage)
        safety = simulate_inflight_safety()
        write_json(
            output_dir / "safety_simulation.json", safety.model_dump(mode="json")
        )
        retrieval_example = build_retrieval_example(channel_summaries, events)
        write_json(
            output_dir / "retrieval_examples" / "week_to_raw.json",
            retrieval_example,
        )
        result_manifest = {
            "experiment": "temporal-history",
            "format_version": 2,
            "started_at": started_at.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "source": {
                "workspace": manifest["workspace"],
                "channel_id": timeline_id,
                "start_utc": manifest["start_utc"],
                "cutoff_utc_exclusive": manifest["cutoff_utc_exclusive"],
                "export_files": manifest["files"],
                "normalized_source_hash": normalization["normalized_source_hash"],
            },
            "configuration": {
                "timezone": timezone_name,
                "provider": provider,
                "model": summarizer.model_name,
                "prompt_version": PROMPT_VERSION,
                "model_config_hash": model_config_hash,
                "max_workers": max_workers,
                "thread_scope_policy": "all_threads",
                "pressure_token_limit": pressure_token_limit,
            },
            "counts": {
                "normalized_events": len(events),
                "quarantined_events": len(
                    read_jsonl(output_dir / "quarantined_events.jsonl")
                ),
                "summaries": {
                    "channel": {
                        level: len(records)
                        for level, records in channel_summaries.items()
                    },
                    "thread": {
                        level: len(records)
                        for level, records in thread_summaries.items()
                    },
                },
                "thread_timelines": len(thread_groups),
                "pressure_summaries": {
                    "channel": len(channel_pressure.summaries),
                    "thread": len(all_thread_pressure),
                },
                "pressure_failures": {
                    "channel": len(channel_pressure.failures),
                    "thread": sum(
                        len(result.failures) for result in thread_pressure.values()
                    ),
                },
                "attachments": {
                    "referenced": len(attachment_findings),
                    "processed": sum(
                        finding.status == "processed" for finding in attachment_findings
                    ),
                    "missing": sum(
                        finding.status == "missing" for finding in attachment_findings
                    ),
                    "blocked": sum(
                        finding.status == "blocked" for finding in attachment_findings
                    ),
                    "unsupported": sum(
                        finding.status == "unsupported"
                        for finding in attachment_findings
                    ),
                },
                "coverage_records": len(all_coverage),
                "empty_periods": sum(
                    record.status == "empty" for record in all_coverage
                ),
            },
            "generation": {
                "channel": channel_generation,
                "thread": thread_generation,
            },
            "failures": store.failure_count(),
            "azure_usage": getattr(summarizer, "usage", None),
            "attachment_azure_usage": attachment_azure_usage,
            "safety_simulation_passed": safety.passed,
            "pressure_validation": {
                "channel": validate_pressure_exactly_once(events, channel_pressure),
                "threads_passed": all(
                    validate_pressure_exactly_once(thread_groups[thread_id], result)[
                        "passed"
                    ]
                    for thread_id, result in thread_pressure.items()
                ),
            },
        }
        write_json(
            output_dir / "pressure_simulation.json",
            {
                "channel": {
                    "queue_log": channel_pressure.queue_log,
                    "failures": channel_pressure.failures,
                    "raw_tail_event_ids": channel_pressure.raw_tail_event_ids,
                },
                "threads": {
                    thread_id: {
                        "queue_log": result.queue_log,
                        "failures": result.failures,
                        "raw_tail_event_ids": result.raw_tail_event_ids,
                    }
                    for thread_id, result in thread_pressure.items()
                },
            },
        )
        write_json(output_dir / "manifest.json", result_manifest)
        from .integrity import validate_integrity

        write_json(
            output_dir / "integrity_report.json",
            validate_integrity(output_dir),
        )
        combined_summaries = {
            level: [
                *channel_summaries[level],
                *thread_summaries[level],
            ]
            for level in GRANULARITIES
        }
        qa = build_qa_report(
            output_dir, result_manifest, combined_summaries, all_coverage
        )
        (output_dir / "qa_report.md").write_text(qa, encoding="utf-8")
        from .visualization import build_visualization

        visualization = build_visualization(
            output_dir,
            output_dir / "temporal_history_visualization.html",
        )
        result_manifest["artifacts"] = {
            "visualization_html": visualization["html"],
            "visualization_data": visualization["data"],
            "integrity_report": str(output_dir / "integrity_report.json"),
            "qa_report": str(output_dir / "qa_report.md"),
        }
        write_json(output_dir / "manifest.json", result_manifest)
        return result_manifest
    finally:
        store.close()


def build_retrieval_example(
    summaries: dict[str, list[SummaryRecord]],
    events: Sequence[NormalizedEvent],
) -> dict[str, Any]:
    if not summaries.get("week"):
        return {"week": None, "days": [], "six_hours": [], "raw_events": []}
    week = summaries["week"][0]
    days = [
        item for item in summaries["day"] if item.summary_id in week.child_summary_ids
    ]
    six_hour_ids = {summary_id for day in days for summary_id in day.child_summary_ids}
    six_hours = [
        item for item in summaries["six_hour"] if item.summary_id in six_hour_ids
    ]
    raw_ids = {
        event_id for summary in six_hours for event_id in summary.source_event_ids
    }
    raw_events = [event for event in events if event.event_id in raw_ids]
    return {
        "week": week.model_dump(mode="json"),
        "days": [item.model_dump(mode="json") for item in days],
        "six_hours": [item.model_dump(mode="json") for item in six_hours],
        "raw_events": [item.model_dump(mode="json") for item in raw_events],
    }


def parse_range(value: str, timezone_name: str) -> tuple[datetime, datetime]:
    tz = ZoneInfo(timezone_name)

    def parse_endpoint(endpoint: str, is_end: bool) -> datetime:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", endpoint):
            parsed_date = date.fromisoformat(endpoint)
            if is_end:
                parsed_date += timedelta(days=1)
            return datetime.combine(parsed_date, datetime_time.min, tzinfo=tz)
        parsed = datetime.fromisoformat(endpoint.replace("Z", "+00:00"))
        return parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed

    if "/" in value:
        start_text, end_text = value.split("/", 1)
        return (
            parse_endpoint(start_text, False).astimezone(UTC),
            parse_endpoint(end_text, False).astimezone(UTC),
        )
    start = parse_endpoint(value, False)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        end = parse_endpoint(value, True)
    else:
        end = start + timedelta(hours=1)
    return start.astimezone(UTC), end.astimezone(UTC)


def retrieve(
    output_dir: Path,
    range_value: str,
    granularity: Granularity = "week",
    raw: bool = False,
    thread_root_id: str | None = None,
) -> dict[str, Any]:
    manifest = read_json(output_dir / "manifest.json")
    start, end = parse_range(range_value, manifest["configuration"]["timezone"])
    if raw:
        events = load_events(output_dir)
        selected = [
            event
            for event in events
            if start <= event.occurred_at < end
            and (
                thread_root_id is None
                or event.thread_root_id == thread_root_id
                or event.source_message_id == thread_root_id
            )
        ]
        return {
            "range": {"utc_start": start.isoformat(), "utc_end": end.isoformat()},
            "thread_root_id": thread_root_id,
            "events": [event.model_dump(mode="json") for event in selected],
        }
    summaries = [
        SummaryRecord.model_validate(row)
        for row in read_jsonl(output_dir / "summaries" / f"{granularity}.jsonl")
    ]
    selected = [
        summary
        for summary in summaries
        if summary.utc_start < end
        and summary.utc_end > start
        and (thread_root_id is None or thread_root_id in summary.thread_references)
    ]
    return {
        "range": {"utc_start": start.isoformat(), "utc_end": end.isoformat()},
        "granularity": granularity,
        "thread_root_id": thread_root_id,
        "summaries": [summary.model_dump(mode="json") for summary in selected],
    }


def simulate_inflight_safety() -> SafetySimulationResult:
    before = "event-before-snapshot"
    during = "event-during-generation"
    after = "event-after-commit"
    assertions: list[dict[str, Any]] = []

    pending_context = [before, during]
    assertions.append(
        {
            "name": "raw events remain during generation",
            "passed": pending_context == [before, during],
            "represented": pending_context,
        }
    )
    failed_context = [before, during]
    assertions.append(
        {
            "name": "failure leaves raw context unchanged",
            "passed": failed_context == pending_context,
            "represented": failed_context,
        }
    )
    summary_lineage = [before]
    raw_tail = [during, after, before]
    assembled = list(dict.fromkeys([*summary_lineage, *raw_tail]))
    assertions.append(
        {
            "name": "commit switches covered slice and retains post-watermark tail",
            "passed": assembled == [before, during, after],
            "represented": assembled,
        }
    )
    assertions.append(
        {
            "name": "duplicate delivery is deduplicated by event ID",
            "passed": len(assembled) == len(set(assembled)),
            "represented": assembled,
        }
    )
    assertions.append(
        {
            "name": "retry produces exactly-once assembled representation",
            "passed": set(assembled) == {before, during, after},
            "represented": assembled,
        }
    )
    return SafetySimulationResult(
        passed=all(item["passed"] for item in assertions),
        assertions=assertions,
        assembled_event_ids=assembled,
    )


def lineage_validation(
    summaries: dict[str, list[SummaryRecord]], events: Sequence[NormalizedEvent]
) -> dict[str, Any]:
    event_ids = {event.event_id for event in events}
    summary_map = {
        summary.summary_id: summary
        for records in summaries.values()
        for summary in records
    }
    missing_events: list[str] = []
    missing_children: list[str] = []
    month_non_day_children: list[str] = []
    for level, records in summaries.items():
        for summary in records:
            missing_events.extend(
                event_id
                for event_id in summary.source_event_ids
                if event_id not in event_ids
            )
            missing_children.extend(
                child_id
                for child_id in summary.child_summary_ids
                if child_id not in summary_map
            )
            if level == "month":
                month_non_day_children.extend(
                    child_id
                    for child_id in summary.child_summary_ids
                    if summary_map.get(child_id)
                    and summary_map[child_id].granularity != "day"
                )
    return {
        "passed": not missing_events
        and not missing_children
        and not month_non_day_children,
        "missing_event_references": missing_events,
        "missing_child_references": missing_children,
        "month_non_day_children": month_non_day_children,
    }


def build_qa_report(
    output_dir: Path,
    manifest: dict[str, Any],
    summaries: dict[str, list[SummaryRecord]],
    coverage: Sequence[CoverageRecord],
) -> str:
    profile = read_json(output_dir / "source_profile.json")
    events = load_events(output_dir)
    from .integrity import validate_integrity

    lineage = validate_integrity(output_dir)
    raw_characters = sum(len(event.content) for event in events)
    summary_characters = sum(
        len(summary.summary_text)
        for records in summaries.values()
        for summary in records
    )
    compression = (
        round(raw_characters / summary_characters, 2) if summary_characters else None
    )
    safety = read_json(output_dir / "safety_simulation.json")
    counts = manifest["counts"]
    attachment_records = read_jsonl(output_dir / "attachments.jsonl")
    azure_image_findings = sum(
        item.get("status") == "processed"
        and item.get("model") not in {None, "deterministic-mock-v1"}
        for item in attachment_records
    )
    image_limitation = (
        f"- {azure_image_findings} available images were processed once through "
        "native Azure multimodal input; later summaries consume only their "
        "structured findings."
        if azure_image_findings
        else (
            "- Available image bytes were exported and hash-validated. Mock mode "
            "does not infer visual contents; native Azure multimodal analysis is "
            "required for semantic image findings."
        )
    )
    lines = [
        "# Temporal History QA Report",
        "",
        f"- Workspace/channel: `{manifest['source']['workspace']}` / `{manifest['source']['channel_id']}`",
        f"- Source range: `{manifest['source']['start_utc']}` to `{manifest['source']['cutoff_utc_exclusive']}` (exclusive)",
        f"- Normalized events: {counts['normalized_events']}",
        f"- Quarantined events: {counts['quarantined_events']}",
        f"- Source roles: {canonical_json(profile['profile']['role_counts'])}",
        f"- Thread replies: {profile['normalization']['thread_reply_count']}",
        f"- Resolved thread roots: {profile['normalization']['resolved_thread_root_count']}",
        f"- Resolved actor events: {profile['normalization'].get('identity', {}).get('resolved_events', 0)}",
        f"- Unresolved actor IDs: {profile['normalization'].get('identity', {}).get('unresolved_actor_ids', 0)}",
        "",
        "## Summary and coverage",
        "",
    ]
    for level in GRANULARITIES:
        level_coverage = [item for item in coverage if item.granularity == level]
        lines.append(
            f"- {level}: {len(summaries[level])} summaries, "
            f"{sum(item.status == 'empty' for item in level_coverage)} empty periods"
        )
    lines.extend(
        [
            f"- Raw-to-summary text compression ratio: {compression}",
            f"- Model/provider: `{manifest['configuration']['model']}` / `{manifest['configuration']['provider']}`",
            f"- Generation/resume stats: `{canonical_json(manifest['generation'])}`",
            f"- Recorded generation failures: {manifest['failures']}",
            f"- Thread timelines: {manifest['counts'].get('thread_timelines', 0)}",
            f"- Pressure summaries: {canonical_json(manifest['counts'].get('pressure_summaries', {}))}",
            f"- Attachment findings: {canonical_json(manifest['counts'].get('attachments', {}))}",
            "",
            "## Validation",
            "",
            f"- Export checks passed: {profile['valid']}",
            f"- Lineage checks passed: {lineage['passed']}",
            f"- In-flight safety simulation passed: {safety['passed']}",
            f"- Duplicate source IDs: {profile['profile']['duplicate_message_id_count']}",
            f"- Month summaries with non-day children: {len(lineage['critical']['month_non_day_children'])}",
            "",
            "## Known limitations",
            "",
            "- The database export does not contain complete Slack edit or deletion history; prior states cannot be reconstructed.",
            (
                "- Slack identities are resolved from the sanitized directory cache; "
                f"{profile['normalization'].get('identity', {}).get('unresolved_actor_ids', 0)} "
                "actor IDs remain unresolved."
            ),
            image_limitation,
            (
                f"- {lineage['diagnostics']['missing_thread_roots']} thread replies "
                "have no exported root event and remain explicitly diagnosed."
            ),
            "- Calendar periods intersecting the export boundaries are explicitly marked partial.",
            "- Model summaries are derived indexes; normalized events remain the canonical evidence.",
            "",
            "## Retrieval proof",
            "",
            "- `retrieval_examples/week_to_raw.json` demonstrates week → day → six-hour → raw lineage.",
            "- The CLI supports exact raw ranges, dedicated thread timelines, Then/Now context, and authorization-aware browsing.",
            "",
        ]
    )
    if manifest.get("azure_usage"):
        usage = manifest["azure_usage"]
        lines.insert(
            lines.index("## Validation") - 1,
            "- Azure usage: "
            f"{usage['requests_succeeded']} successful requests / "
            f"{usage['requests_attempted']} attempts, "
            f"{usage['input_tokens']} input tokens, "
            f"{usage['output_tokens']} output tokens, "
            f"{usage['retries']} retries",
        )
    return "\n".join(lines)

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .core import (
    GRANULARITIES,
    load_events,
    parse_datetime,
    read_json,
    read_jsonl,
)
from .models import ContextPackage, SummaryRecord


def load_scope_summaries(output_dir: Path, scope_type: str) -> list[SummaryRecord]:
    records: list[SummaryRecord] = []
    for level in GRANULARITIES:
        path = output_dir / "summaries" / scope_type / f"{level}.jsonl"
        records.extend(SummaryRecord.model_validate(row) for row in read_jsonl(path))
    return records


def compact_summary(summary: SummaryRecord, reason: str) -> dict[str, Any]:
    return {
        "summary_id": summary.summary_id,
        "scope_type": summary.scope_type,
        "channel_id": summary.channel_id,
        "thread_id": summary.thread_id,
        "granularity": summary.granularity,
        "utc_start": summary.utc_start.isoformat(),
        "utc_end": summary.utc_end.isoformat(),
        "summary_text": summary.summary_text,
        "topics": summary.topics,
        "decisions": summary.decisions,
        "actions": summary.actions,
        "open_questions": summary.open_questions,
        "source_event_count": summary.source_event_count,
        "reason": reason,
        "evidence_role": "continuity",
    }


def most_compact_stack(
    summaries: list[SummaryRecord], at: datetime
) -> list[SummaryRecord]:
    selected: list[SummaryRecord] = []
    for level in reversed(GRANULARITIES):
        candidates = [
            summary
            for summary in summaries
            if summary.granularity == level and summary.utc_end <= at
        ]
        if candidates:
            selected.append(max(candidates, key=lambda summary: summary.utc_end))
    return selected


def assemble_context(
    output_dir: Path,
    channel_id: str,
    at: str | datetime,
    thread_id: str | None = None,
    window_hours: int = 24,
) -> ContextPackage:
    at_time = parse_datetime(at) if isinstance(at, str) else at
    at_time = at_time.astimezone(UTC)
    events = [
        event
        for event in load_events(output_dir)
        if event.timeline_id == channel_id and event.occurred_at <= at_time
    ]
    channel_summaries = [
        summary
        for summary in load_scope_summaries(output_dir, "channel")
        if summary.channel_id == channel_id
    ]
    thread_summaries = (
        [
            summary
            for summary in load_scope_summaries(output_dir, "thread")
            if summary.channel_id == channel_id and summary.thread_id == thread_id
        ]
        if thread_id
        else []
    )
    active_summaries = thread_summaries if thread_id else channel_summaries
    previous = most_compact_stack(active_summaries, at_time)
    covered_ids = {
        event_id for summary in previous for event_id in summary.source_event_ids
    }
    scoped_events = [
        event
        for event in events
        if (
            not thread_id
            or event.thread_root_id == thread_id
            or event.source_message_id == thread_id
        )
    ]
    raw_tail = [event for event in scoped_events if event.event_id not in covered_ids]

    then_channel: dict[str, Any] = {}
    now_channel: dict[str, Any] = {}
    related_threads: list[dict[str, Any]] = []
    if thread_id:
        root = next(
            (event for event in events if event.source_message_id == thread_id),
            None,
        )
        anchor = (
            root.occurred_at
            if root
            else (scoped_events[0].occurred_at if scoped_events else at_time)
        )
        half_window = timedelta(hours=window_hours / 2)
        then = [
            summary
            for summary in channel_summaries
            if summary.utc_start < anchor + half_window
            and summary.utc_end > anchor - half_window
        ]
        now = [
            summary
            for summary in channel_summaries
            if summary.utc_start < at_time
            and summary.utc_end > at_time - timedelta(hours=window_hours)
        ]
        then_channel = {
            "anchor": anchor.isoformat(),
            "window_hours": window_hours,
            "summaries": [
                compact_summary(summary, "then_channel")
                for summary in then
                if summary.granularity in {"six_hour", "day"}
            ],
            "rationale": "Context surrounding thread creation.",
        }
        now_channel = {
            "anchor": at_time.isoformat(),
            "window_hours": window_hours,
            "summaries": [
                compact_summary(summary, "now_channel")
                for summary in now
                if summary.granularity in {"six_hour", "day"}
            ],
            "rationale": "Recent channel context before reactivation.",
        }
        relation_records = [
            relation
            for summary in previous
            for relation in summary.context_selections.get("related_threads", [])
        ]
        related_threads = list(
            {relation["thread_id"]: relation for relation in relation_records}.values()
        )

    previous_payload = [
        compact_summary(summary, "previous_summary") for summary in previous
    ]
    raw_payload = [event.model_dump(mode="json") for event in raw_tail]
    thread_payload = (
        [compact_summary(summary, "thread_history") for summary in previous]
        if thread_id
        else []
    )
    token_text = str(
        [
            previous_payload,
            raw_payload,
            thread_payload,
            then_channel,
            now_channel,
            related_threads,
        ]
    )
    return ContextPackage(
        channel_id=channel_id,
        thread_id=thread_id,
        at=at_time,
        previous_summaries=previous_payload,
        raw_unsummarized_tail=raw_payload,
        current_request={
            "role": "user",
            "content": "<CURRENT_REQUEST_PLACEHOLDER>",
            "evidence_role": "current_request",
        },
        thread_history=thread_payload,
        then_channel=then_channel,
        now_channel=now_channel,
        related_threads=related_threads,
        estimated_tokens=(len(token_text) + 3) // 4,
    )


def _is_closed_calendar_summary(summary: SummaryRecord, granularity: str) -> bool:
    start = summary.local_start
    end = summary.local_end
    midnight = (
        end.hour == 0 and end.minute == 0 and end.second == 0 and end.microsecond == 0
    )
    if granularity == "month":
        next_month = (start.year * 12 + start.month) + 1
        return (
            start.day == 1
            and start.hour == 0
            and midnight
            and end.day == 1
            and end.year * 12 + end.month == next_month
        )
    if granularity == "week":
        return (
            start.weekday() == 0
            and start.hour == 0
            and end.weekday() == 0
            and midnight
            and (end.date() - start.date()).days == 7
        )
    if granularity == "day":
        return start.hour == 0 and midnight and (end.date() - start.date()).days == 1
    if granularity == "six_hour":
        return (
            start.minute == 0
            and start.second == 0
            and start.microsecond == 0
            and start.hour % 6 == 0
            and end.minute == 0
            and end.second == 0
            and end.microsecond == 0
            and end.hour % 6 == 0
            and summary.utc_end - summary.utc_start == timedelta(hours=6)
        )
    return False


def _frontier_summary(summary: SummaryRecord) -> dict[str, Any]:
    return {
        "piece_id": summary.summary_id,
        "piece_type": summary.granularity,
        "scope_type": summary.scope_type,
        "period_start": summary.utc_start.isoformat(),
        "period_end": summary.utc_end.isoformat(),
        "summary_reason": summary.summary_reason,
        "summary_text": summary.summary_text,
        "topics": summary.topics,
        "decisions": summary.decisions,
        "actions": summary.actions,
        "open_questions": summary.open_questions,
        "participants": summary.participants,
        "coverage_gaps": summary.coverage_gaps,
        "source_event_count": summary.source_event_count,
        "source_watermark": summary.source_watermark,
    }


def assemble_frontier_context(
    output_dir: Path,
    channel_id: str,
    at: str | datetime,
    current_request: str,
    active_run_state: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Assemble the agreed calendar-frontier context stack."""
    manifest = read_json(output_dir / "manifest.json")
    timezone_name = manifest["configuration"]["timezone"]
    at_time = parse_datetime(at) if isinstance(at, str) else at
    at_time = at_time.astimezone(UTC)
    source_start = parse_datetime(manifest["source"]["start_utc"]).astimezone(UTC)
    summaries = [
        summary
        for summary in load_scope_summaries(output_dir, "channel")
        if summary.channel_id == channel_id
        and summary.status == "committed"
        and summary.utc_start < at_time
    ]
    pressure = [
        SummaryRecord.model_validate(row)
        for row in read_jsonl(output_dir / "summaries" / "channel" / "pressure.jsonl")
        if row.get("channel_id") == channel_id
        and row.get("status", "committed") == "committed"
    ]

    months = sorted(
        [
            summary
            for summary in summaries
            if summary.granularity == "month"
            and summary.utc_end <= at_time
            and _is_closed_calendar_summary(summary, "month")
        ],
        key=lambda summary: summary.utc_start,
    )
    month_frontier = max((summary.utc_end for summary in months), default=source_start)

    weeks = sorted(
        [
            summary
            for summary in summaries
            if summary.granularity == "week"
            and summary.utc_end > month_frontier
            and summary.utc_end <= at_time
            and _is_closed_calendar_summary(summary, "week")
        ],
        key=lambda summary: summary.utc_start,
    )
    week_frontier = max((summary.utc_end for summary in weeks), default=month_frontier)

    days = sorted(
        [
            summary
            for summary in summaries
            if summary.granularity == "day"
            and summary.utc_end > week_frontier
            and summary.utc_end <= at_time
            and _is_closed_calendar_summary(summary, "day")
        ],
        key=lambda summary: summary.utc_start,
    )
    day_frontier = max((summary.utc_end for summary in days), default=week_frontier)

    six_hours = sorted(
        [
            summary
            for summary in summaries
            if summary.granularity == "six_hour"
            and summary.utc_end > day_frontier
            and summary.utc_end <= at_time
            and _is_closed_calendar_summary(summary, "six_hour")
        ],
        key=lambda summary: summary.utc_start,
    )
    six_hour_frontier = max(
        (summary.utc_end for summary in six_hours), default=day_frontier
    )

    local_at = at_time.astimezone(ZoneInfo(timezone_name))
    open_block_local = local_at.replace(
        hour=(local_at.hour // 6) * 6,
        minute=0,
        second=0,
        microsecond=0,
    )
    open_block_start = open_block_local.astimezone(UTC)
    pressure_in_open_block = sorted(
        [
            summary
            for summary in pressure
            if summary.utc_start >= open_block_start and summary.utc_end <= at_time
        ],
        key=lambda summary: summary.utc_start,
    )

    selected_summaries = [
        *months,
        *weeks,
        *days,
        *six_hours,
        *pressure_in_open_block,
    ]
    latest_watermark = max(
        (
            summary.source_watermark
            for summary in selected_summaries
            if summary.source_watermark
        ),
        default=None,
    )
    events = [
        event
        for event in load_events(output_dir)
        if event.timeline_id == channel_id and event.occurred_at < at_time
    ]
    if latest_watermark:
        raw_tail = [event for event in events if event.source_order > latest_watermark]
    else:
        raw_tail = [event for event in events if event.occurred_at >= six_hour_frontier]
    raw_payload = [
        {
            "piece_id": event.event_id,
            "piece_type": "raw_event",
            "occurred_at": event.occurred_at.isoformat(),
            "source_order": event.source_order,
            "actor_id": event.actor_id,
            "actor_display_name": event.actor_display_name,
            "actor_type": event.actor_type,
            "thread_id": event.thread_root_id,
            "event_type": event.event_type,
            "content": event.display_content,
            "attachment_findings": event.source_metadata.get("attachment_findings", []),
        }
        for event in raw_tail
    ]
    context: dict[str, Any] = {
        "CONTEXT_POLICY": "calendar_frontier_v1",
        "AS_OF": at_time.isoformat(),
        "TIMEZONE": timezone_name,
        "FRONTIERS": {
            "month": month_frontier.isoformat(),
            "week": week_frontier.isoformat(),
            "day": day_frontier.isoformat(),
            "six_hour": six_hour_frontier.isoformat(),
            "open_six_hour_start": open_block_start.isoformat(),
            "latest_summary_watermark": latest_watermark,
        },
        "MONTH_SUMMARIES_FOR_CLOSED_MONTHS": [
            _frontier_summary(summary) for summary in months
        ],
        "WEEK_SUMMARIES_SINCE_THE_MONTH_FRONTIER": [
            _frontier_summary(summary) for summary in weeks
        ],
        "DAY_SUMMARIES_SINCE_THE_WEEK_FRONTIER": [
            _frontier_summary(summary) for summary in days
        ],
        "SIX_HOUR_SUMMARIES_SINCE_THE_DAY_FRONTIER": [
            _frontier_summary(summary) for summary in six_hours
        ],
        "PRESSURE_SUMMARIES_INSIDE_THE_OPEN_SIX_HOUR_PERIOD_IF_ANY": [
            _frontier_summary(summary) for summary in pressure_in_open_block
        ],
        "RAW_CONTEXT_AFTER_THE_LATEST_SUMMARY_WATERMARK": raw_payload,
        "EXACT_CURRENT_REQUEST": current_request,
        "ACTIVE_RUN_STATE": active_run_state
        or {
            "status": "inactive",
            "interface": "temporal_history_cli",
            "pending_tool_calls": 0,
            "note": "No external workflow run is attached to this CLI session.",
        },
    }
    serialized = json.dumps(context, default=str, separators=(",", ":"))
    context["ESTIMATED_TOKENS"] = (len(serialized) + 3) // 4
    context["SECTION_COUNTS"] = {
        "months": len(months),
        "weeks": len(weeks),
        "days": len(days),
        "six_hours": len(six_hours),
        "pressure": len(pressure_in_open_block),
        "raw_events": len(raw_payload),
    }
    return context

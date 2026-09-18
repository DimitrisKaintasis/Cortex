from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from .context_assembly import load_scope_summaries
from .core import GRANULARITIES, load_events, read_jsonl
from .models import SummaryRecord


def duplicates(values: list[str]) -> list[str]:
    return sorted(value for value, count in Counter(values).items() if count > 1)


def validate_integrity(output_dir: Path) -> dict[str, Any]:
    events = load_events(output_dir)
    event_ids = [event.event_id for event in events]
    channel = load_scope_summaries(output_dir, "channel")
    threads = load_scope_summaries(output_dir, "thread")
    pressure = [
        SummaryRecord.model_validate(row)
        for scope in ("channel", "thread")
        for row in read_jsonl(output_dir / "summaries" / scope / "pressure.jsonl")
    ]
    all_summaries = [*channel, *threads, *pressure]
    summary_map = {summary.summary_id: summary for summary in all_summaries}

    channel_six_hour_ids = [
        event_id
        for summary in channel
        if summary.granularity == "six_hour"
        for event_id in summary.source_event_ids
    ]
    uncovered_channel = sorted(set(event_ids) - set(channel_six_hour_ids))
    duplicate_channel_coverage = duplicates(channel_six_hour_ids)

    thread_expected: dict[str, set[str]] = {}
    roots = {
        event.source_message_id: event.event_id
        for event in events
        if not event.is_thread_reply
    }
    for event in events:
        if not event.thread_root_id:
            continue
        thread_expected.setdefault(event.thread_root_id, set()).add(event.event_id)
        if event.thread_root_id in roots:
            thread_expected[event.thread_root_id].add(roots[event.thread_root_id])
    thread_covered: dict[str, list[str]] = {}
    for summary in threads:
        if summary.granularity == "six_hour" and summary.thread_id:
            thread_covered.setdefault(summary.thread_id, []).extend(
                summary.source_event_ids
            )
    thread_gaps = {
        thread_id: sorted(expected - set(thread_covered.get(thread_id, [])))
        for thread_id, expected in thread_expected.items()
        if expected - set(thread_covered.get(thread_id, []))
    }
    thread_duplicates = {
        thread_id: duplicates(covered)
        for thread_id, covered in thread_covered.items()
        if duplicates(covered)
    }

    missing_child_refs = sorted(
        {
            child_id
            for summary in [*channel, *threads]
            for child_id in summary.child_summary_ids
            if child_id not in summary_map
        }
    )
    month_non_day_children = sorted(
        {
            child_id
            for summary in [*channel, *threads]
            if summary.granularity == "month"
            for child_id in summary.child_summary_ids
            if child_id in summary_map and summary_map[child_id].granularity != "day"
        }
    )
    pressure_duplicate_coverage = duplicates(
        [
            f"{summary.scope_type}:{summary.thread_id or '-'}:{event_id}"
            for summary in pressure
            for event_id in summary.source_event_ids
        ]
    )
    unmerged_pressure = sorted(
        summary.summary_id
        for summary in pressure
        if not any(
            summary.summary_id in scheduled.child_summary_ids
            for scheduled in [*channel, *threads]
            if scheduled.granularity == "six_hour"
            and scheduled.scope_type == summary.scope_type
            and scheduled.thread_id == summary.thread_id
        )
    )
    unresolved_identities = sum(
        event.actor_id is not None and event.actor_resolution_source == "unresolved"
        for event in events
    )
    missing_thread_roots = sum(
        event.is_thread_reply and event.parent_event_id is None for event in events
    )
    attachments = read_jsonl(output_dir / "attachments.jsonl")
    missing_attachments = sum(
        item.get("status") in {"missing", "failed"} for item in attachments
    )
    quarantined = read_jsonl(output_dir / "quarantined_events.jsonl")

    critical = {
        "duplicate_event_ids": duplicates(event_ids),
        "uncovered_channel_events": uncovered_channel,
        "duplicate_channel_six_hour_coverage": duplicate_channel_coverage,
        "thread_coverage_gaps": thread_gaps,
        "thread_duplicate_coverage": thread_duplicates,
        "missing_child_references": missing_child_refs,
        "month_non_day_children": month_non_day_children,
        "pressure_duplicate_coverage": pressure_duplicate_coverage,
        "unmerged_pressure_summaries": unmerged_pressure,
    }
    return {
        "passed": not any(critical.values()),
        "critical": critical,
        "diagnostics": {
            "valid_events": len(events),
            "channel_summaries": len(channel),
            "thread_summaries": len(threads),
            "pressure_summaries": len(pressure),
            "unresolved_identities": unresolved_identities,
            "missing_thread_roots": missing_thread_roots,
            "missing_attachments": missing_attachments,
            "quarantined_events": len(quarantined),
            "failed_coverage_records": sum(
                row.get("status") == "failed"
                for row in read_jsonl(output_dir / "coverage.jsonl")
            ),
        },
        "calendar_levels": list(GRANULARITIES),
    }

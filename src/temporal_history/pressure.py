from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from .core import TemporalSummarizer, period_start, stable_hash
from .models import NormalizedEvent, Period, SummaryRecord


def estimate_event_tokens(event: NormalizedEvent) -> int:
    return (len(event.display_content) + 3) // 4 + 64


def estimate_events_tokens(events: Sequence[NormalizedEvent]) -> int:
    return sum(estimate_event_tokens(event) for event in events)


def estimate_summary_tokens(summary: SummaryRecord) -> int:
    content = " ".join(
        [
            summary.summary_text,
            *summary.topics,
            *summary.decisions,
            *summary.actions,
            *summary.open_questions,
        ]
    )
    return (len(content) + 3) // 4 + 96


@dataclass
class PressureSimulation:
    summaries: list[SummaryRecord] = field(default_factory=list)
    queue_log: list[dict[str, Any]] = field(default_factory=list)
    failures: list[dict[str, Any]] = field(default_factory=list)
    raw_tail_event_ids: list[str] = field(default_factory=list)


def simulate_pressure_compaction(
    *,
    events: Sequence[NormalizedEvent],
    summarizer: TemporalSummarizer,
    channel_id: str,
    thread_id: str | None,
    timezone_name: str,
    token_limit: int,
    existing_summaries: Sequence[SummaryRecord] = (),
    on_commit: Callable[[SummaryRecord], None] | None = None,
) -> PressureSimulation:
    simulation = PressureSimulation()
    raw_buffer: list[NormalizedEvent] = []
    continuity_summaries: list[SummaryRecord] = []
    raw_tail_event_ids: list[str] = []
    current_block: datetime | None = None
    for event in sorted(
        events,
        key=lambda item: (item.occurred_at, item.source_order, item.event_id),
    ):
        event_block = period_start(
            event.occurred_at, "six_hour", timezone_name
        ).astimezone(UTC)
        if current_block is not None and event_block != current_block:
            raw_tail_event_ids.extend(item.event_id for item in raw_buffer)
            raw_buffer = []
            continuity_summaries = []
        current_block = event_block
        projected = estimate_events_tokens([*raw_buffer, event]) + sum(
            estimate_summary_tokens(summary) for summary in continuity_summaries
        )
        if projected <= token_limit:
            raw_buffer.append(event)
            continue
        simulation.queue_log.append(
            {
                "event_id": event.event_id,
                "state": "queued_for_compaction",
                "projected_tokens": projected,
                "six_hour_block": event_block.isoformat(),
            }
        )
        blocked = False
        while projected > token_limit:
            target_tokens = max(1, token_limit // 2)
            compactable: list[NormalizedEvent] = []
            compacted_tokens = 0
            for candidate in raw_buffer:
                compactable.append(candidate)
                compacted_tokens += estimate_event_tokens(candidate)
                if compacted_tokens >= target_tokens:
                    break
            if not compactable:
                simulation.failures.append(
                    {
                        "event_id": event.event_id,
                        "reason": (
                            "incoming_request_exceeds_limit_without_compactable_history"
                        ),
                        "projected_tokens": projected,
                        "queue_blocked": True,
                    }
                )
                simulation.queue_log.append(
                    {"event_id": event.event_id, "state": "blocked"}
                )
                blocked = True
                break

            start = compactable[0].occurred_at
            end = compactable[-1].occurred_at + timedelta(microseconds=1)
            period = Period(
                timeline_id=channel_id
                if not thread_id
                else f"{channel_id}:thread:{thread_id}",
                scope_type="thread" if thread_id else "channel",
                channel_id=channel_id,
                thread_id=thread_id,
                granularity="six_hour",
                timezone=timezone_name,
                local_start=start,
                local_end=end,
                utc_start=start.astimezone(UTC),
                utc_end=end.astimezone(UTC),
            )
            continuity_payload = [
                {
                    "summary_id": summary.summary_id,
                    "summary_text": summary.summary_text,
                    "topics": summary.topics,
                    "decisions": summary.decisions,
                    "actions": summary.actions,
                    "open_questions": summary.open_questions,
                    "source_watermark": summary.source_watermark,
                    "evidence_role": "continuity_only",
                    "advances_watermark": False,
                }
                for summary in continuity_summaries
            ]
            source_hash = stable_hash(
                {
                    "new_source_evidence": [
                        item.model_dump(mode="json") for item in compactable
                    ],
                    "continuity_summaries": [
                        {
                            "summary_id": summary.summary_id,
                            "version": summary.version,
                            "source_hash": summary.source_hash,
                        }
                        for summary in continuity_summaries
                    ],
                }
            )
            summary_id = (
                "pressure_"
                + stable_hash(
                    {
                        "scope": period.timeline_id,
                        "source_hash": source_hash,
                        "token_limit": token_limit,
                    }
                )[:24]
            )
            existing = next(
                (
                    summary
                    for summary in existing_summaries
                    if summary.summary_id == summary_id
                    and summary.model == summarizer.model_name
                    and summary.model_config_hash
                    == stable_hash(summarizer.config_for_hash)
                ),
                None,
            )
            if existing:
                summary_record = existing
            else:
                result = summarizer.summarize_raw_period(
                    period,
                    compactable,
                    {
                        "summary_reason": "pressure",
                        "pressure_token_limit": token_limit,
                        "queued_event_id": event.event_id,
                        "new_source_evidence": {
                            "event_ids": [item.event_id for item in compactable],
                            "advances_watermark": True,
                        },
                        "previous_summaries": continuity_payload,
                    },
                )
                summary_record = SummaryRecord(
                    summary_id=summary_id,
                    timeline_id=period.timeline_id,
                    scope_type=period.scope_type,
                    channel_id=channel_id,
                    thread_id=thread_id,
                    summary_reason="pressure",
                    granularity="pressure",
                    timezone=timezone_name,
                    local_start=start,
                    local_end=end,
                    utc_start=start.astimezone(UTC),
                    utc_end=end.astimezone(UTC),
                    version=1,
                    source_watermark=compactable[-1].source_order,
                    source_event_count=len(compactable),
                    source_event_ids=[item.event_id for item in compactable],
                    child_summary_ids=[],
                    **result.content.model_dump(),
                    model=summarizer.model_name,
                    prompt_version="temporal-history-v2-pressure-continuity",
                    generated_at=datetime.now(UTC),
                    source_hash=source_hash,
                    model_config_hash=stable_hash(summarizer.config_for_hash),
                    retry_count=result.retry_count,
                    context_selections={
                        "queued_event_id": event.event_id,
                        "pressure_token_limit": token_limit,
                        "six_hour_block": event_block.isoformat(),
                        "previous_pressure_summary_ids": [
                            summary.summary_id for summary in continuity_summaries
                        ],
                        "previous_summary_role": "continuity_only",
                    },
                )
            simulation.summaries.append(summary_record)
            continuity_summaries.append(summary_record)
            if on_commit:
                on_commit(summary_record)
            compacted_ids = {item.event_id for item in compactable}
            raw_buffer = [
                item for item in raw_buffer if item.event_id not in compacted_ids
            ]
            simulation.queue_log.append(
                {
                    "event_id": event.event_id,
                    "state": "compaction_committed",
                    "summary_id": summary_id,
                }
            )
            projected = estimate_events_tokens([*raw_buffer, event]) + sum(
                estimate_summary_tokens(summary) for summary in continuity_summaries
            )
        if blocked:
            continue
        raw_buffer.append(event)
        simulation.queue_log.append(
            {"event_id": event.event_id, "state": "released_in_order"}
        )
    raw_tail_event_ids.extend(item.event_id for item in raw_buffer)
    simulation.raw_tail_event_ids = raw_tail_event_ids
    return simulation


def validate_pressure_exactly_once(
    events: Sequence[NormalizedEvent],
    simulation: PressureSimulation,
) -> dict[str, Any]:
    covered = [
        event_id
        for summary in simulation.summaries
        for event_id in summary.source_event_ids
    ]
    represented = [*covered, *simulation.raw_tail_event_ids]
    expected = {
        event.event_id
        for event in events
        if event.event_id
        not in {failure["event_id"] for failure in simulation.failures}
    }
    return {
        "passed": len(represented) == len(set(represented))
        and set(represented) == expected,
        "covered_event_count": len(covered),
        "raw_tail_event_count": len(simulation.raw_tail_event_ids),
        "duplicate_event_ids": [
            event_id for event_id in set(represented) if represented.count(event_id) > 1
        ],
        "missing_event_ids": sorted(expected - set(represented)),
        "blocked_event_ids": [failure["event_id"] for failure in simulation.failures],
    }

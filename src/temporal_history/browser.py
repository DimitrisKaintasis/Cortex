from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .authorization import ContextAuthorization
from .context_assembly import load_scope_summaries
from .core import load_events, parse_range, read_json


def lexical_score(query: str, text: str) -> int:
    terms = {term.lower() for term in re.findall(r"[A-Za-z0-9_-]{2,}", query)}
    lowered = text.lower()
    return sum(lowered.count(term) for term in terms)


def browse_history(
    *,
    output_dir: Path,
    actor_id: str,
    channel_id: str,
    query: str,
    range_value: str,
    authorization: ContextAuthorization,
    thread_id: str | None = None,
) -> dict[str, Any]:
    # Authorization happens before loading or returning candidate metadata.
    if not authorization.can_view_channel(actor_id, channel_id):
        return {"authorized": False, "summaries": [], "raw_events": []}
    if thread_id and not authorization.can_view_thread(actor_id, channel_id, thread_id):
        return {"authorized": False, "summaries": [], "raw_events": []}

    manifest = read_json(output_dir / "manifest.json")
    start, end = parse_range(range_value, manifest["configuration"]["timezone"])
    scope = "thread" if thread_id else "channel"
    ranked_summaries: list[tuple[int, dict[str, Any]]] = []
    for summary in load_scope_summaries(output_dir, scope):
        if summary.channel_id != channel_id:
            continue
        if summary.thread_id and not authorization.can_view_thread(
            actor_id, channel_id, summary.thread_id
        ):
            continue
        if thread_id and summary.thread_id != thread_id:
            continue
        if not (summary.utc_start < end and summary.utc_end > start):
            continue
        searchable = " ".join(
            [
                summary.summary_text,
                *summary.topics,
                *summary.decisions,
                *summary.actions,
                *summary.open_questions,
            ]
        )
        score = lexical_score(query, searchable)
        if query and score == 0:
            continue
        ranked_summaries.append(
            (
                score,
                {
                    "summary_id": summary.summary_id,
                    "scope_type": summary.scope_type,
                    "thread_id": summary.thread_id,
                    "granularity": summary.granularity,
                    "utc_start": summary.utc_start.isoformat(),
                    "utc_end": summary.utc_end.isoformat(),
                    "summary_text": summary.summary_text,
                    "topics": summary.topics,
                    "decisions": summary.decisions,
                    "actions": summary.actions,
                    "child_summary_ids": summary.child_summary_ids,
                    "source_event_ids": summary.source_event_ids,
                    "score": score,
                },
            )
        )

    raw_events: list[dict[str, Any]] = []
    for event in load_events(output_dir):
        if event.timeline_id != channel_id or not (start <= event.occurred_at < end):
            continue
        if event.thread_root_id and not authorization.can_view_thread(
            actor_id, channel_id, event.thread_root_id
        ):
            continue
        if (
            thread_id
            and event.thread_root_id != thread_id
            and (event.source_message_id != thread_id)
        ):
            continue
        score = lexical_score(query, event.display_content)
        if query and score == 0:
            continue
        payload = event.model_dump(mode="json")
        attachment_ids = event.source_metadata.get("uploaded_file_ids") or []
        allowed_ids = [
            attachment_id
            for attachment_id in attachment_ids
            if authorization.can_view_attachment(actor_id, str(attachment_id))
        ]
        payload["source_metadata"] = {
            "role": event.source_metadata.get("role"),
            "attachment_ids": allowed_ids,
        }
        payload["score"] = score
        raw_events.append(payload)

    ranked_summaries.sort(key=lambda item: (-item[0], item[1]["utc_start"]))
    raw_events.sort(key=lambda item: (-item["score"], item["occurred_at"]))
    return {
        "authorized": True,
        "query_mode": "lexical_experiment",
        "range": {"utc_start": start.isoformat(), "utc_end": end.isoformat()},
        "summaries": [payload for _, payload in ranked_summaries],
        "raw_events": raw_events,
    }

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .context_assembly import assemble_frontier_context, load_scope_summaries
from .core import (
    AzureOpenAISummarizer,
    load_events,
    parse_range,
    read_json,
    read_jsonl,
)
from .models import NormalizedEvent, SummaryRecord

PieceType = Literal[
    "raw_event",
    "pressure",
    "six_hour",
    "day",
    "week",
    "month",
    "year",
]
ScopeFilter = Literal["channel", "thread", "any"]

PIECE_TYPES: tuple[PieceType, ...] = (
    "raw_event",
    "pressure",
    "six_hour",
    "day",
    "week",
    "month",
    "year",
)

SEARCH_TOOL: dict[str, Any] = {
    "type": "function",
    "name": "search_temporal_history",
    "description": (
        "Search one temporal-history piece type over an explicit time period. "
        "Use coarse summaries first, then narrower summaries or raw_event for "
        "evidence. Period end is exclusive."
    ),
    "strict": True,
    "parameters": {
        "type": "object",
        "properties": {
            "query": {
                "type": "string",
                "description": (
                    "Lexical terms to rank and filter pieces. Use an empty "
                    "string to retrieve all pieces in the period."
                ),
            },
            "piece_type": {
                "type": "string",
                "enum": list(PIECE_TYPES),
                "description": "Exact piece type to search.",
            },
            "period_start": {
                "type": "string",
                "description": (
                    "Inclusive ISO date or timestamp in the history timezone."
                ),
            },
            "period_end": {
                "type": "string",
                "description": (
                    "Exclusive ISO date or timestamp in the history timezone."
                ),
            },
            "scope_type": {
                "type": "string",
                "enum": ["channel", "thread", "any"],
                "description": "Channel summaries, thread summaries, or both.",
            },
            "thread_id": {
                "type": "string",
                "description": (
                    "Exact thread root ID, or an empty string for no thread filter."
                ),
            },
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "Maximum pieces returned.",
            },
        },
        "required": [
            "query",
            "piece_type",
            "period_start",
            "period_end",
            "scope_type",
            "thread_id",
            "limit",
        ],
        "additionalProperties": False,
    },
}


def searchable_summary_text(summary: SummaryRecord) -> str:
    return " ".join(
        [
            summary.summary_text,
            *summary.topics,
            *summary.decisions,
            *summary.actions,
            *summary.open_questions,
            *summary.participants,
            *summary.coverage_gaps,
        ]
    )


def lexical_score(query: str, text: str) -> int:
    terms = {
        term.casefold()
        for term in query.replace("/", " ").replace("-", " ").split()
        if len(term) >= 2
    }
    lowered = text.casefold()
    return sum(lowered.count(term) for term in terms)


def format_tool_trace(arguments: dict[str, Any], result: dict[str, Any]) -> str:
    query = arguments.get("query") or "(all pieces)"
    scope = str(arguments.get("scope_type", "any"))
    thread_id = arguments.get("thread_id")
    if thread_id:
        scope += f" · thread {thread_id}"
    error = result.get("error")
    outcome = (
        f"error: {error}"
        if error
        else (
            f"{result.get('returned_count', 0)} returned from "
            f"{result.get('matched_count', 0)} matches"
        )
    )
    return (
        "\nTEMPORAL SEARCH\n"
        f"  Type    {arguments.get('piece_type', '(missing)')}\n"
        f"  Period  {arguments.get('period_start', '(missing)')} -> "
        f"{arguments.get('period_end', '(missing)')}\n"
        f"  Scope   {scope}\n"
        f"  Query   {query}\n"
        f"  Result  {outcome}"
    )


def format_frontier_trace(context: dict[str, Any]) -> str:
    counts = context["SECTION_COUNTS"]
    return (
        "\nFRONTIER CONTEXT\n"
        f"  As of      {context['AS_OF']}\n"
        f"  Summaries  {counts['months']} month · {counts['weeks']} week · "
        f"{counts['days']} day · {counts['six_hours']} six-hour · "
        f"{counts['pressure']} pressure\n"
        f"  Raw tail   {counts['raw_events']} events\n"
        f"  Estimate   {context['ESTIMATED_TOKENS']:,} tokens"
    )


class TemporalHistorySearch:
    """In-memory, read-only search over generated history artefacts."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = output_dir
        self.manifest = read_json(output_dir / "manifest.json")
        self.timezone = self.manifest["configuration"]["timezone"]
        self.channel_id = self.manifest["source"]["channel_id"]
        self.summaries = [
            *load_scope_summaries(output_dir, "channel"),
            *load_scope_summaries(output_dir, "thread"),
            *[
                SummaryRecord.model_validate(row)
                for scope in ("channel", "thread")
                for row in read_jsonl(
                    output_dir / "summaries" / scope / "pressure.jsonl"
                )
            ],
        ]
        self.events = load_events(output_dir)
        self.source_start = self.manifest["source"]["start_utc"]
        self.source_end = self.manifest["source"]["cutoff_utc_exclusive"]

    def metadata(self) -> dict[str, Any]:
        return {
            "channel_id": self.channel_id,
            "timezone": self.timezone,
            "source_period": {
                "start_inclusive": self.source_start,
                "end_exclusive": self.source_end,
            },
            "piece_types": list(PIECE_TYPES),
            "summary_count": len(self.summaries),
            "event_count": len(self.events),
        }

    def search(
        self,
        *,
        query: str,
        piece_type: PieceType,
        period_start: str,
        period_end: str,
        scope_type: ScopeFilter,
        thread_id: str,
        limit: int,
    ) -> dict[str, Any]:
        if piece_type not in PIECE_TYPES:
            raise ValueError(f"Unsupported piece_type: {piece_type}")
        if scope_type not in {"channel", "thread", "any"}:
            raise ValueError(f"Unsupported scope_type: {scope_type}")
        limit = max(1, min(50, int(limit)))
        start, end = parse_range(f"{period_start}/{period_end}", self.timezone)
        if start >= end:
            raise ValueError("period_start must be before period_end")

        if piece_type == "raw_event":
            matches = self._search_events(
                query=query,
                start=start,
                end=end,
                scope_type=scope_type,
                thread_id=thread_id,
            )
        else:
            matches = self._search_summaries(
                query=query,
                piece_type=piece_type,
                start=start,
                end=end,
                scope_type=scope_type,
                thread_id=thread_id,
            )
        matches.sort(
            key=lambda item: (
                -item["score"],
                item["period_start"],
                item["piece_id"],
            )
        )
        selected = matches[:limit]
        return {
            "query": query,
            "piece_type": piece_type,
            "scope_type": scope_type,
            "thread_id": thread_id or None,
            "period": {
                "start_inclusive": start.isoformat(),
                "end_exclusive": end.isoformat(),
            },
            "matched_count": len(matches),
            "returned_count": len(selected),
            "results": selected,
        }

    def _search_summaries(
        self,
        *,
        query: str,
        piece_type: PieceType,
        start: datetime,
        end: datetime,
        scope_type: ScopeFilter,
        thread_id: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for summary in self.summaries:
            if summary.granularity != piece_type:
                continue
            if scope_type != "any" and summary.scope_type != scope_type:
                continue
            if thread_id and summary.thread_id != thread_id:
                continue
            if not (summary.utc_start < end and summary.utc_end > start):
                continue
            searchable = searchable_summary_text(summary)
            score = lexical_score(query, searchable)
            if query and score == 0:
                continue
            results.append(
                {
                    "piece_id": summary.summary_id,
                    "piece_type": summary.granularity,
                    "scope_type": summary.scope_type,
                    "channel_id": summary.channel_id,
                    "thread_id": summary.thread_id,
                    "period_start": summary.utc_start.isoformat(),
                    "period_end": summary.utc_end.isoformat(),
                    "summary_reason": summary.summary_reason,
                    "summary_text": summary.summary_text[:8000],
                    "topics": summary.topics,
                    "decisions": summary.decisions,
                    "actions": summary.actions,
                    "open_questions": summary.open_questions,
                    "participants": summary.participants,
                    "coverage_gaps": summary.coverage_gaps,
                    "source_event_count": summary.source_event_count,
                    "source_event_ids": summary.source_event_ids[:100],
                    "source_event_ids_truncated": (len(summary.source_event_ids) > 100),
                    "child_summary_ids": summary.child_summary_ids,
                    "score": score,
                }
            )
        return results

    def _search_events(
        self,
        *,
        query: str,
        start: datetime,
        end: datetime,
        scope_type: ScopeFilter,
        thread_id: str,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        for event in self.events:
            if not (start <= event.occurred_at < end):
                continue
            event_thread_id = event.thread_root_id or (
                event.source_message_id if event.is_thread_reply else None
            )
            if scope_type == "thread" and not event_thread_id:
                continue
            if thread_id and event_thread_id != thread_id:
                continue
            searchable = " ".join(
                [
                    event.display_content,
                    event.actor_display_name or "",
                    event.thread_root_excerpt or "",
                ]
            )
            score = lexical_score(query, searchable)
            if query and score == 0:
                continue
            results.append(self._event_payload(event, event_thread_id, score))
        return results

    @staticmethod
    def _event_payload(
        event: NormalizedEvent, thread_id: str | None, score: int
    ) -> dict[str, Any]:
        findings = event.source_metadata.get("attachment_findings", [])
        return {
            "piece_id": event.event_id,
            "piece_type": "raw_event",
            "scope_type": "thread" if thread_id else "channel",
            "channel_id": event.timeline_id,
            "thread_id": thread_id,
            "period_start": event.occurred_at.isoformat(),
            "period_end": event.occurred_at.isoformat(),
            "actor_id": event.actor_id,
            "actor_display_name": event.actor_display_name,
            "actor_type": event.actor_type,
            "event_type": event.event_type,
            "content": event.display_content[:6000],
            "content_truncated": len(event.display_content) > 6000,
            "thread_root_excerpt": event.thread_root_excerpt,
            "attachment_findings": findings,
            "score": score,
        }


class TemporalHistoryAgent:
    def __init__(
        self,
        output_dir: Path,
        *,
        max_tool_rounds: int = 8,
        show_tools: bool = False,
    ) -> None:
        self.search = TemporalHistorySearch(output_dir)
        self.provider = AzureOpenAISummarizer()
        if not self.provider.use_responses_api:
            raise ValueError(
                "The temporal-history agent requires a Responses API model"
            )
        self.max_tool_rounds = max_tool_rounds
        self.show_tools = show_tools
        self.history: list[dict[str, str]] = []
        self.last_frontier_context: dict[str, Any] | None = None

    def ask(self, question: str) -> str:
        metadata = self.search.metadata()
        frontier_context = assemble_frontier_context(
            self.search.output_dir,
            self.search.channel_id,
            metadata["source_period"]["end_exclusive"],
            question,
            active_run_state={
                "status": "active",
                "interface": "temporal_history_cli",
                "phase": "before_first_model_call",
                "pending_tool_calls": 0,
                "available_tools": ["search_temporal_history"],
                "external_workflow_state": "not_attached",
                "prior_conversation_turns": len(self.history),
            },
        )
        self.last_frontier_context = frontier_context
        if self.show_tools:
            print(
                format_frontier_trace(frontier_context),
                file=sys.stderr,
                flush=True,
            )
        system = (
            "You are a read-only temporal-history research agent. Answer questions "
            "only from the preassembled frontier context and tool evidence. The "
            "frontier context is the default continuity stack and is organized into "
            "closed months, weeks since the month frontier, days since the week "
            "frontier, six-hour summaries since the day frontier, pressure "
            "summaries in the open six-hour period, and raw events after the latest "
            "summary watermark. The history covers "
            f"{metadata['source_period']['start_inclusive']} through "
            f"{metadata['source_period']['end_exclusive']} (exclusive), timezone "
            f"{metadata['timezone']}. Use search_temporal_history when the frontier "
            "stack is insufficient, when the user asks about a narrower historical "
            "period, or when exact evidence is needed. The tool requires a piece "
            "type and explicit period. Start with the coarsest useful type, then "
            "narrow to day, six_hour, pressure, or raw_event. Try alternate lexical "
            "queries when a search is empty. Distinguish channel summaries from "
            "thread summaries. Cite supporting piece IDs inline in square brackets. "
            "Never invent facts or imply that an action completed when evidence only "
            "says it was proposed. Clearly state coverage gaps and uncertainty. This "
            "is a terminal interface: output plain text only. Do not use Markdown "
            "headings, bullets, bold markers, code fences, tables, or links. Use "
            "short paragraphs; if a list is needed, prefix lines with 1), 2), and "
            "so on. Keep evidence citations as plain square-bracketed piece IDs."
        )
        input_items: list[Any] = [
            {"role": "system", "content": system},
            {
                "role": "system",
                "content": (
                    "PREASSEMBLED TEMPORAL FRONTIER CONTEXT\n"
                    + json.dumps(
                        frontier_context,
                        default=str,
                        separators=(",", ":"),
                    )
                ),
            },
            *self.history[-8:],
            {"role": "user", "content": question},
        ]
        for _ in range(self.max_tool_rounds):
            response = self.provider.create_response(
                input=input_items,
                tools=[SEARCH_TOOL],
                tool_choice="auto",
            )
            calls = [
                item
                for item in response.output
                if getattr(item, "type", None) == "function_call"
            ]
            if not calls:
                answer = response.output_text.strip()
                if not answer:
                    raise RuntimeError("The model returned no answer")
                self.history.extend(
                    [
                        {"role": "user", "content": question},
                        {"role": "assistant", "content": answer},
                    ]
                )
                return answer

            input_items.extend(response.output)
            for call in calls:
                if call.name != "search_temporal_history":
                    result: dict[str, Any] = {"error": f"Unknown tool: {call.name}"}
                    arguments: dict[str, Any] = {}
                else:
                    try:
                        arguments = json.loads(call.arguments)
                        result = self.search.search(**arguments)
                    except Exception as exc:
                        result = {
                            "error": str(exc),
                            "arguments": arguments if "arguments" in locals() else {},
                        }
                if self.show_tools:
                    print(
                        format_tool_trace(arguments, result),
                        file=sys.stderr,
                        flush=True,
                    )
                input_items.append(
                    {
                        "type": "function_call_output",
                        "call_id": call.call_id,
                        "output": json.dumps(result, default=str),
                    }
                )
        raise RuntimeError(f"The agent exceeded {self.max_tool_rounds} tool rounds")

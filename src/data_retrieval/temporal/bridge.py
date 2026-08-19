from __future__ import annotations

import json
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from temporal_history.core import (
    GRANULARITIES,
    MockSummarizer,
    StateStore,
    TemporalSummarizer,
    run_scope_hierarchy,
    stable_hash,
)
from temporal_history.models import NormalizedEvent, SummaryRecord

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    Document,
    IngestionBundle,
)

BRIDGE_VERSION = "temporal-atom-bridge-v1"
ACTOR_TYPES = {"human", "bot", "app", "agent", "system", "unknown"}
ACTOR_RESOLUTION_SOURCES = {"slack_api", "export", "cache", "unresolved"}


@dataclass(frozen=True, slots=True)
class TemporalProjectionResult:
    """A persistable summary-atom bundle plus generation diagnostics."""

    bundle: IngestionBundle
    summary_counts: dict[str, int]
    coverage_count: int
    generation: dict[str, dict[str, int]]


class TemporalBridge:
    """Translate between canonical Data Retrieval atoms and Temporal History."""

    def __init__(self, summarizer: TemporalSummarizer | None = None) -> None:
        self.summarizer = summarizer or MockSummarizer()

    def project(
        self,
        *,
        namespace: str,
        timeline_id: str,
        atoms: Sequence[Atom],
        timezone_name: str,
        range_start: datetime,
        range_end: datetime,
        state_path: Path,
        max_workers: int = 1,
    ) -> TemporalProjectionResult:
        namespace = namespace.strip()
        timeline_id = timeline_id.strip()
        if not namespace:
            raise ValueError("namespace cannot be empty")
        if not timeline_id:
            raise ValueError("timeline_id cannot be empty")
        self._require_aware("range_start", range_start)
        self._require_aware("range_end", range_end)
        range_start = range_start.astimezone(UTC)
        range_end = range_end.astimezone(UTC)
        if range_start >= range_end:
            raise ValueError("range_start must be before range_end")
        if not atoms:
            raise ValueError("at least one atom is required")

        events = self._to_events(
            namespace=namespace,
            timeline_id=timeline_id,
            atoms=atoms,
            range_start=range_start,
            range_end=range_end,
        )
        model_config_hash = stable_hash(
            {
                "bridge_version": BRIDGE_VERSION,
                "summarizer": self.summarizer.config_for_hash,
            }
        )
        store = StateStore(state_path)
        try:
            summaries, coverage, generation = run_scope_hierarchy(
                scope_type="channel",
                channel_id=timeline_id,
                thread_id=None,
                events=events,
                context_events=events,
                supporting_thread_summaries=(),
                supporting_pressure_summaries=(),
                timezone_name=timezone_name,
                range_start=range_start,
                range_end=range_end,
                summarizer=self.summarizer,
                store=store,
                model_config_hash=model_config_hash,
                max_workers=max_workers,
            )
        finally:
            store.close()

        bundle = self._to_bundle(
            namespace=namespace,
            timeline_id=timeline_id,
            events=events,
            summaries=summaries,
            timezone_name=timezone_name,
            range_start=range_start,
            range_end=range_end,
            model_config_hash=model_config_hash,
        )
        return TemporalProjectionResult(
            bundle=bundle,
            summary_counts={level: len(summaries[level]) for level in GRANULARITIES},
            coverage_count=len(coverage),
            generation=generation,
        )

    def _to_events(
        self,
        *,
        namespace: str,
        timeline_id: str,
        atoms: Sequence[Atom],
        range_start: datetime,
        range_end: datetime,
    ) -> list[NormalizedEvent]:
        seen_ids: set[str] = set()
        events: list[NormalizedEvent] = []
        for atom in atoms:
            if atom.atom_id in seen_ids:
                raise ValueError(f"duplicate atom_id: {atom.atom_id}")
            seen_ids.add(atom.atom_id)
            if atom.namespace != namespace:
                raise ValueError(f"atom {atom.atom_id} belongs to another namespace")
            if atom.occurred_at is None:
                raise ValueError(f"atom {atom.atom_id} has no occurred_at timestamp")
            self._require_aware(f"atom {atom.atom_id} occurred_at", atom.occurred_at)
            self._require_aware(f"atom {atom.atom_id} created_at", atom.created_at)
            occurred_at = atom.occurred_at.astimezone(UTC)
            if not range_start <= occurred_at < range_end:
                raise ValueError(f"atom {atom.atom_id} falls outside the requested half-open range")

            metadata = dict(atom.metadata)
            actor_type = str(metadata.get("actor_type", "unknown"))
            if actor_type not in ACTOR_TYPES:
                actor_type = "unknown"
            actor_resolution_source = str(metadata.get("actor_resolution_source", "unresolved"))
            if actor_resolution_source not in ACTOR_RESOLUTION_SOURCES:
                actor_resolution_source = "unresolved"
            actor_resolution_timestamp = metadata.get("actor_resolution_timestamp")
            if isinstance(actor_resolution_timestamp, str):
                actor_resolution_timestamp = datetime.fromisoformat(actor_resolution_timestamp)
            if actor_resolution_timestamp is not None:
                self._require_aware("actor_resolution_timestamp", actor_resolution_timestamp)
            recorded_at = metadata.get("recorded_at", atom.created_at)
            if isinstance(recorded_at, str):
                recorded_at = datetime.fromisoformat(recorded_at)
            if not isinstance(recorded_at, datetime):
                raise ValueError("recorded_at must be an ISO-8601 datetime")
            self._require_aware("recorded_at", recorded_at)
            source_order = str(
                metadata.get("source_order")
                or self._source_order(occurred_at, atom.position, atom.atom_id)
            )
            display_content = str(metadata.get("display_content") or atom.content)
            source_message_id = str(metadata.get("source_message_id") or atom.atom_id)
            events.append(
                NormalizedEvent(
                    event_id=atom.atom_id,
                    timeline_id=timeline_id,
                    event_type=str(metadata.get("event_type", "atom")),
                    occurred_at=occurred_at,
                    recorded_at=recorded_at.astimezone(UTC),
                    source_order=source_order,
                    actor_id=self._optional_str(metadata.get("actor_id")),
                    actor_display_name=self._optional_str(metadata.get("actor_display_name")),
                    actor_type=actor_type,
                    actor_resolution_source=actor_resolution_source,
                    actor_resolution_timestamp=actor_resolution_timestamp,
                    actor_is_active=metadata.get("actor_is_active"),
                    raw_content=atom.content,
                    display_content=display_content,
                    source_message_id=source_message_id,
                    thread_root_id=self._optional_str(metadata.get("thread_root_id")),
                    is_thread_reply=bool(metadata.get("is_thread_reply", False)),
                    thread_root_excerpt=self._optional_str(metadata.get("thread_root_excerpt")),
                    parent_event_id=self._optional_str(metadata.get("parent_event_id")),
                    supersedes_event_id=self._optional_str(metadata.get("supersedes_event_id")),
                    payload_reference=self._optional_str(metadata.get("payload_reference")),
                    source_metadata={
                        **metadata,
                        "data_retrieval_document_id": atom.document_id,
                        "data_retrieval_content_hash": atom.content_hash,
                    },
                )
            )
        return sorted(
            events,
            key=lambda event: (event.occurred_at, event.source_order, event.event_id),
        )

    def _to_bundle(
        self,
        *,
        namespace: str,
        timeline_id: str,
        events: Sequence[NormalizedEvent],
        summaries: dict[str, list[SummaryRecord]],
        timezone_name: str,
        range_start: datetime,
        range_end: datetime,
        model_config_hash: str,
    ) -> IngestionBundle:
        ordered_summaries = sorted(
            (summary for level in GRANULARITIES for summary in summaries[level]),
            key=lambda summary: (
                summary.utc_start,
                GRANULARITIES.index(summary.granularity),
                summary.summary_id,
            ),
        )
        input_fingerprint = content_hash(
            json.dumps(
                {
                    "events": [event.event_id for event in events],
                    "timeline_id": timeline_id,
                    "timezone": timezone_name,
                    "range_start": range_start.isoformat(),
                    "range_end": range_end.isoformat(),
                    "model_config_hash": model_config_hash,
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        document_id = stable_id(
            "doc", namespace, "temporal_projection", timeline_id, input_fingerprint
        )
        document = Document(
            document_id=document_id,
            namespace=namespace,
            source=f"temporal-history:{timeline_id}",
            content_hash=input_fingerprint,
            metadata={
                "kind": "temporal_projection",
                "bridge_version": BRIDGE_VERSION,
                "timeline_id": timeline_id,
                "timezone": timezone_name,
                "range_start": range_start.isoformat(),
                "range_end": range_end.isoformat(),
                "model_config_hash": model_config_hash,
            },
        )
        atom_ids_by_summary = {
            summary.summary_id: stable_id("atom", "temporal_summary", summary.summary_id)
            for summary in ordered_summaries
        }
        summary_atoms = tuple(
            Atom(
                atom_id=atom_ids_by_summary[summary.summary_id],
                document_id=document_id,
                namespace=namespace,
                position=position,
                char_start=0,
                char_end=len(summary.summary_text),
                content=summary.summary_text,
                content_hash=content_hash(summary.summary_text),
                kind=AtomKind.TEMPORAL_SUMMARY,
                occurred_at=summary.utc_end,
                created_at=summary.generated_at,
                metadata={
                    "engine": "temporal-history",
                    "engine_summary_id": summary.summary_id,
                    "timeline_id": summary.timeline_id,
                    "scope_type": summary.scope_type,
                    "channel_id": summary.channel_id,
                    "thread_id": summary.thread_id,
                    "granularity": summary.granularity,
                    "summary_reason": summary.summary_reason,
                    "timezone": summary.timezone,
                    "local_start": summary.local_start.isoformat(),
                    "local_end": summary.local_end.isoformat(),
                    "period_start": summary.utc_start.isoformat(),
                    "period_end": summary.utc_end.isoformat(),
                    "status": summary.status,
                    "version": summary.version,
                    "source_watermark": summary.source_watermark,
                    "source_event_count": summary.source_event_count,
                    "source_event_ids": summary.source_event_ids,
                    "child_summary_ids": summary.child_summary_ids,
                    "topics": summary.topics,
                    "decisions": summary.decisions,
                    "actions": summary.actions,
                    "open_questions": summary.open_questions,
                    "participants": summary.participants,
                    "coverage_gaps": summary.coverage_gaps,
                    "thread_references": summary.thread_references,
                    "artefact_references": summary.artefact_references,
                    "embedded_thread_summaries": summary.embedded_thread_summaries,
                    "context_selections": summary.context_selections,
                    "model": summary.model,
                    "prompt_version": summary.prompt_version,
                    "source_hash": summary.source_hash,
                    "model_config_hash": summary.model_config_hash,
                    "retry_count": summary.retry_count,
                },
            )
            for position, summary in enumerate(ordered_summaries)
        )
        links: list[AtomLink] = []
        for summary in ordered_summaries:
            from_atom_id = atom_ids_by_summary[summary.summary_id]
            links.extend(
                AtomLink(
                    from_atom_id=from_atom_id,
                    to_atom_id=source_atom_id,
                    relation=AtomLinkRelation.SUMMARIZES,
                    created_at=summary.generated_at,
                    metadata={"engine_summary_id": summary.summary_id},
                )
                for source_atom_id in summary.source_event_ids
            )
            links.extend(
                AtomLink(
                    from_atom_id=from_atom_id,
                    to_atom_id=atom_ids_by_summary[child_summary_id],
                    relation=AtomLinkRelation.DERIVED_FROM,
                    created_at=summary.generated_at,
                    metadata={
                        "engine_summary_id": summary.summary_id,
                        "child_engine_summary_id": child_summary_id,
                    },
                )
                for child_summary_id in summary.child_summary_ids
                if child_summary_id in atom_ids_by_summary
            )
        return IngestionBundle(
            document=document,
            atoms=summary_atoms,
            tags=(),
            atom_tags=(),
            atom_links=tuple(links),
        )

    @staticmethod
    def _source_order(occurred_at: datetime, position: int, atom_id: str) -> str:
        microseconds = int(occurred_at.timestamp() * 1_000_000)
        return f"{microseconds:020d}:{position:012d}:{atom_id}"

    @staticmethod
    def _optional_str(value: Any) -> str | None:
        return None if value is None or value == "" else str(value)

    @staticmethod
    def _require_aware(name: str, value: datetime) -> None:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError(f"{name} must include a timezone")

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .core import AzureOpenAISummarizer, read_jsonl, write_jsonl
from .models import AttachmentFinding, NormalizedEvent


class ImageAnalysis(BaseModel):
    description: str
    visible_text: list[str] = Field(default_factory=list)
    entities: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    actions: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


def attachment_sources(
    events: Sequence[NormalizedEvent],
) -> dict[str, list[NormalizedEvent]]:
    sources: dict[str, list[NormalizedEvent]] = {}
    for event in events:
        for attachment_id in event.source_metadata.get("uploaded_file_ids") or []:
            sources.setdefault(str(attachment_id), []).append(event)
    return sources


def analyze_image_native(
    summarizer: AzureOpenAISummarizer,
    payload: bytes,
    mime_type: str,
) -> ImageAnalysis:
    data_url = f"data:{mime_type};base64," + base64.b64encode(payload).decode("ascii")
    parsed, _ = summarizer.parse_responses(
        input_payload=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": (
                            "Extract evidence from this image without guessing. "
                            "Identify visible text, entities, decisions, actions, "
                            "and risks. Mark unknowns by omission."
                        ),
                    },
                    {"type": "input_image", "image_url": data_url},
                ],
            }
        ],
        text_format=ImageAnalysis,
    )
    return parsed


def process_attachments(
    *,
    events: list[NormalizedEvent],
    raw_attachment_dir: Path,
    output_path: Path,
    provider: str,
    max_workers: int = 3,
) -> tuple[list[NormalizedEvent], list[AttachmentFinding], dict[str, int] | None]:
    sources = attachment_sources(events)
    existing = {
        row["attachment_id"]: AttachmentFinding.model_validate(row)
        for row in read_jsonl(output_path)
    }
    manifest_path = raw_attachment_dir / "attachment_manifest.json"
    manifest = (
        json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_path.exists()
        else {"attachments": []}
    )
    exported = {item["attachment_id"]: item for item in manifest.get("attachments", [])}
    azure = AzureOpenAISummarizer() if provider == "azure" and sources else None

    def build_finding(item: tuple[str, list[NormalizedEvent]]) -> AttachmentFinding:
        attachment_id, source_events = item
        source = source_events[0]
        record = exported.get(attachment_id)
        if not record:
            return AttachmentFinding(
                attachment_id=attachment_id,
                source_event_id=source.event_id,
                channel_id=source.timeline_id,
                thread_id=source.thread_root_id,
                status="missing",
                limitation="Attachment bytes were not present in the export.",
            )
        if record.get("status") != "exported":
            raw_status = record.get("status", "unavailable")
            status = (
                "blocked"
                if raw_status == "blocked_by_compliance"
                else "unsupported"
                if raw_status == "unsupported_non_image"
                else "missing"
            )
            return AttachmentFinding(
                attachment_id=attachment_id,
                content_hash=record.get("sha256"),
                mime_type=record.get("mime_type"),
                source_event_id=source.event_id,
                channel_id=source.timeline_id,
                thread_id=source.thread_root_id,
                status=status,
                limitation=f"Attachment export status: {raw_status}.",
            )
        path = raw_attachment_dir / record["archive_path"]
        payload = path.read_bytes()
        content_hash = hashlib.sha256(payload).hexdigest()
        cached = existing.get(attachment_id)
        cache_matches_provider = bool(
            cached
            and cached.content_hash == content_hash
            and (provider != "azure" or (azure and cached.model == azure.model_name))
        )
        if cache_matches_provider and cached:
            return cached
        if provider == "azure" and azure:
            analysis = analyze_image_native(
                azure,
                payload,
                record.get("mime_type")
                or mimetypes.guess_type(path.name)[0]
                or "image/png",
            )
            return AttachmentFinding(
                attachment_id=attachment_id,
                content_hash=content_hash,
                mime_type=record.get("mime_type"),
                source_event_id=source.event_id,
                channel_id=source.timeline_id,
                thread_id=source.thread_root_id,
                status="processed",
                **analysis.model_dump(),
                model=azure.model_name,
                generated_at=datetime.now(UTC),
            )
        return AttachmentFinding(
            attachment_id=attachment_id,
            content_hash=content_hash,
            mime_type=record.get("mime_type"),
            source_event_id=source.event_id,
            channel_id=source.timeline_id,
            thread_id=source.thread_root_id,
            status="processed",
            description=(
                "Image bytes are available and hash-validated. "
                "Mock mode does not infer visual contents."
            ),
            model="deterministic-mock-v1",
            generated_at=datetime.now(UTC),
            limitation="Visual contents require the Azure multimodal provider.",
        )

    items = sorted(sources.items())
    finding_map: dict[str, AttachmentFinding] = {}
    if azure and max_workers > 1:
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {executor.submit(build_finding, item): item[0] for item in items}
            for future in as_completed(futures):
                finding = future.result()
                finding_map[finding.attachment_id] = finding
                write_jsonl(
                    output_path,
                    [finding_map[key] for key in sorted(finding_map)],
                )
    else:
        for item in items:
            finding = build_finding(item)
            finding_map[finding.attachment_id] = finding
            if azure:
                write_jsonl(
                    output_path,
                    [finding_map[key] for key in sorted(finding_map)],
                )
    findings = [finding_map[key] for key in sorted(finding_map)]

    finding_by_event: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        finding_by_event.setdefault(finding.source_event_id, []).append(
            finding.model_dump(mode="json")
        )
    enriched = []
    for event in events:
        metadata = dict(event.source_metadata)
        metadata["attachment_findings"] = finding_by_event.get(event.event_id, [])
        enriched.append(event.model_copy(update={"source_metadata": metadata}))
    write_jsonl(output_path, findings)
    return enriched, findings, azure.usage if azure else None

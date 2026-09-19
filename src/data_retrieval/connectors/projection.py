from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from data_retrieval.connectors.codec import record_to_mapping
from data_retrieval.connectors.contracts import Record, RecordRef, Scope
from data_retrieval.core.identifiers import stable_id


class ConnectorServingState(StrEnum):
    ACTIVE = "active"
    TOMBSTONED = "tombstoned"


@dataclass(frozen=True, slots=True)
class ConnectorRecordProjection:
    record: RecordRef
    namespace: str
    document_id: str
    atom_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    projected_at: datetime
    serving_state: ConnectorServingState = ConnectorServingState.ACTIVE

    def __post_init__(self) -> None:
        if self.record.external_version is None:
            raise ValueError("connector projection requires a record version")
        if not self.namespace.strip():
            raise ValueError("connector projection namespace cannot be empty")
        if not self.document_id.strip():
            raise ValueError("connector projection document_id cannot be empty")
        if not self.atom_ids:
            raise ValueError("connector projection requires at least one atom")
        if len(self.atom_ids) != len(self.evidence_ids):
            raise ValueError("connector atom and evidence identifiers must align")
        if len(self.atom_ids) != len(set(self.atom_ids)):
            raise ValueError("connector projection atom identifiers must be unique")
        if len(self.evidence_ids) != len(set(self.evidence_ids)):
            raise ValueError("connector projection evidence identifiers must be unique")
        if self.projected_at.tzinfo is None or self.projected_at.utcoffset() is None:
            raise ValueError("connector projected_at must include a timezone")


def scope_namespace(scope: Scope) -> str:
    """Return a stable internal routing key; this is not an authorization decision."""

    encoded = json.dumps(scope.key, separators=(",", ":"), ensure_ascii=False).encode()
    return f"connector:{hashlib.sha256(encoded).hexdigest()}"


def projection_source(record: RecordRef) -> str:
    return stable_id(
        "connector-source",
        record.source.source_system,
        record.source.source_instance,
        record.external_id,
        record.external_version or "",
    )


def projection_metadata(record: Record) -> dict[str, object]:
    encoded = record_to_mapping(record, include_source=True)
    return {
        "connector_record": {
            "source": encoded["source"],
            "external_id": record.ref.external_id,
            "external_version": record.ref.external_version,
        },
        "connector_scope": encoded["scope"],
        "connector_metadata": encoded["metadata"],
        "timeline_id": stable_id(
            "connector-timeline", *record.ref.object_key
        ),
        "state_key": stable_id("connector-state", *record.ref.object_key),
    }


def evidence_id(atom_id: str) -> str:
    return stable_id("evidence", atom_id)

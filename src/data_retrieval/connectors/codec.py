from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import Any

from data_retrieval.connectors.contracts import (
    ConnectorCapability,
    ContextPack,
    ContributionPolicy,
    EvidenceResult,
    Outcome,
    OutcomeValue,
    Query,
    Record,
    RecordModality,
    RecordPayload,
    RecordRef,
    Relation,
    Scope,
    Source,
    SourceRef,
    SyncBatch,
    SyncBatchAcknowledgement,
    SyncCommitAcknowledgement,
    SyncItemFailure,
    SyncItemType,
    SyncMode,
    SyncRun,
    TemporalQueryMode,
    Tombstone,
    Visibility,
)


def _mapping(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{name} must be an object")
    if any(not isinstance(key, str) for key in value):
        raise ValueError(f"{name} keys must be strings")
    return value


def _reject_unknown(data: Mapping[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ValueError(f"{name} contains unknown fields: {', '.join(unknown)}")


def _sequence(value: object, name: str) -> Sequence[object]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise ValueError(f"{name} must be an array")
    return value


def _required_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")
    return value


def _optional_string(data: Mapping[str, Any], key: str) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string when provided")
    return value


def _required_bool(data: Mapping[str, Any], key: str) -> bool:
    value = data.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"{key} must be a boolean")
    return value


def _optional_int(data: Mapping[str, Any], key: str) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{key} must be an integer when provided")
    return value


def _required_int(data: Mapping[str, Any], key: str) -> int:
    value = _optional_int(data, key)
    if value is None:
        raise ValueError(f"{key} must be an integer")
    return value


def _required_datetime(data: Mapping[str, Any], key: str) -> datetime:
    value = _required_string(data, key)
    return _parse_datetime(value, key)


def _optional_datetime(data: Mapping[str, Any], key: str) -> datetime | None:
    value = _optional_string(data, key)
    return _parse_datetime(value, key) if value is not None else None


def _parse_datetime(value: str, name: str) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise ValueError(f"{name} must be an ISO-8601 datetime") from error


def _enum[EnumT: StrEnum](enum_type: type[EnumT], value: object, name: str) -> EnumT:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    try:
        return enum_type(value)
    except ValueError as error:
        raise ValueError(f"invalid {name}: {value}") from error


def _metadata(data: Mapping[str, Any]) -> dict[str, Any]:
    value = data.get("metadata", {})
    return dict(_mapping(value, "metadata"))


def _plain_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {str(key): _plain_json(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_plain_json(item) for item in value]
    return value


def _source_ref_mapping(source: SourceRef) -> dict[str, object]:
    return {
        "source_system": source.source_system,
        "source_instance": source.source_instance,
    }


def _scope_mapping(scope: Scope) -> dict[str, object]:
    return {
        "organization_id": scope.organization_id,
        "project_id": scope.project_id,
        "user_id": scope.user_id,
        "device_id": scope.device_id,
        "visibility": scope.visibility.value,
        "contribution_policy": scope.contribution_policy.value,
    }


def source_to_mapping(source: Source) -> dict[str, object]:
    return {
        "source": _source_ref_mapping(source.source),
        "connector_id": source.connector_id,
        "connector_version": source.connector_version,
        "owner_scope": _scope_mapping(source.owner_scope),
        "capabilities": [capability.value for capability in source.capabilities],
        "metadata": _plain_json(source.metadata),
    }


def sync_run_to_mapping(run: SyncRun) -> dict[str, object]:
    return {
        "request_id": run.request_id,
        "source": _source_ref_mapping(run.source),
        "mode": run.mode.value,
        "started_at": run.started_at.isoformat(),
        "previous_cursor": run.previous_cursor,
        "proposed_cursor": run.proposed_cursor,
    }


def record_to_mapping(record: Record, *, include_source: bool = False) -> dict[str, object]:
    payload: dict[str, object] = {
        "inline": record.payload.inline,
        "reference_uri": record.payload.reference_uri,
        "content_hash": record.payload.content_hash,
        "media_type": record.payload.media_type,
    }
    result: dict[str, object] = {
        "external_id": record.ref.external_id,
        "external_version": record.ref.external_version,
        "modality": record.modality.value,
        "payload": payload,
        "scope": _scope_mapping(record.scope),
        "observed_at": record.observed_at.isoformat(),
        "occurred_at": record.occurred_at.isoformat() if record.occurred_at else None,
        "explicit_tags": list(record.explicit_tags),
        "metadata": _plain_json(record.metadata),
    }
    if include_source:
        result["source"] = _source_ref_mapping(record.ref.source)
    return result


def _record_ref_mapping(record: RecordRef, *, include_source: bool) -> dict[str, object]:
    result: dict[str, object] = {
        "external_id": record.external_id,
        "external_version": record.external_version,
    }
    if include_source:
        result["source"] = _source_ref_mapping(record.source)
    return result


def relation_to_mapping(relation: Relation) -> dict[str, object]:
    return {
        "relation_id": relation.relation_id,
        "relation_version": relation.relation_version,
        "relation_type": relation.relation_type,
        "source": _record_ref_mapping(relation.source, include_source=False),
        "target": _record_ref_mapping(
            relation.target,
            include_source=relation.target.source != relation.source.source,
        ),
        "scope": _scope_mapping(relation.scope),
        "observed_at": relation.observed_at.isoformat(),
        "occurred_at": relation.occurred_at.isoformat() if relation.occurred_at else None,
        "confidence": relation.confidence,
        "metadata": _plain_json(relation.metadata),
    }


def tombstone_to_mapping(tombstone: Tombstone) -> dict[str, object]:
    return {
        "external_id": tombstone.record.external_id,
        "tombstone_version": tombstone.tombstone_version,
        "observed_at": tombstone.observed_at.isoformat(),
        "reason": tombstone.reason,
    }


def sync_batch_to_mapping(batch: SyncBatch) -> dict[str, object]:
    return {
        "batch_id": batch.batch_id,
        "sequence": batch.sequence,
        "run": sync_run_to_mapping(batch.run),
        "records": [record_to_mapping(record) for record in batch.records],
        "relations": [relation_to_mapping(relation) for relation in batch.relations],
        "tombstones": [tombstone_to_mapping(item) for item in batch.tombstones],
    }


def _strings(value: object, name: str) -> tuple[str, ...]:
    values = _sequence(value, name)
    if any(not isinstance(item, str) for item in values):
        raise ValueError(f"{name} values must be strings")
    return tuple(values)  # type: ignore[arg-type]


def _source_ref(data: Mapping[str, Any]) -> SourceRef:
    _reject_unknown(data, {"source_system", "source_instance"}, "source reference")
    return SourceRef(
        source_system=_required_string(data, "source_system"),
        source_instance=_required_string(data, "source_instance"),
    )


def _scope(data: Mapping[str, Any]) -> Scope:
    _reject_unknown(
        data,
        {
            "visibility",
            "organization_id",
            "project_id",
            "user_id",
            "device_id",
            "contribution_policy",
        },
        "scope",
    )
    return Scope(
        visibility=_enum(Visibility, data.get("visibility"), "visibility"),
        organization_id=_optional_string(data, "organization_id"),
        project_id=_optional_string(data, "project_id"),
        user_id=_optional_string(data, "user_id"),
        device_id=_optional_string(data, "device_id"),
        contribution_policy=_enum(
            ContributionPolicy,
            data.get("contribution_policy", ContributionPolicy.PRIVATE.value),
            "contribution_policy",
        ),
    )


def _record_ref(data: Mapping[str, Any], default_source: SourceRef | None = None) -> RecordRef:
    _reject_unknown(data, {"source", "external_id", "external_version"}, "record reference")
    source_data = data.get("source")
    source = (
        _source_ref(_mapping(source_data, "source")) if source_data is not None else default_source
    )
    if source is None:
        raise ValueError("record reference requires source")
    return RecordRef(
        source=source,
        external_id=_required_string(data, "external_id"),
        external_version=_optional_string(data, "external_version"),
    )


def source_from_mapping(value: object) -> Source:
    data = _mapping(value, "source registration")
    _reject_unknown(
        data,
        {
            "source",
            "connector_id",
            "connector_version",
            "owner_scope",
            "capabilities",
            "metadata",
        },
        "source registration",
    )
    capabilities = tuple(
        _enum(ConnectorCapability, item, "capability")
        for item in _sequence(data.get("capabilities"), "capabilities")
    )
    return Source(
        source=_source_ref(_mapping(data.get("source"), "source")),
        connector_id=_required_string(data, "connector_id"),
        connector_version=_required_string(data, "connector_version"),
        owner_scope=_scope(_mapping(data.get("owner_scope"), "owner_scope")),
        capabilities=capabilities,
        metadata=_metadata(data),
    )


def sync_batch_from_mapping(value: object) -> SyncBatch:
    data = _mapping(value, "sync batch")
    _reject_unknown(
        data,
        {"batch_id", "sequence", "run", "records", "relations", "tombstones"},
        "sync batch",
    )
    run_data = _mapping(data.get("run"), "run")
    _reject_unknown(
        run_data,
        {
            "request_id",
            "source",
            "mode",
            "started_at",
            "previous_cursor",
            "proposed_cursor",
        },
        "sync run",
    )
    source = _source_ref(_mapping(run_data.get("source"), "source"))
    run = SyncRun(
        request_id=_required_string(run_data, "request_id"),
        source=source,
        mode=_enum(SyncMode, run_data.get("mode"), "sync mode"),
        started_at=_required_datetime(run_data, "started_at"),
        previous_cursor=_optional_string(run_data, "previous_cursor"),
        proposed_cursor=_optional_string(run_data, "proposed_cursor"),
    )
    records = tuple(
        _record(_mapping(item, "record"), source)
        for item in _sequence(data.get("records", []), "records")
    )
    relations = tuple(
        _relation(_mapping(item, "relation"), source)
        for item in _sequence(data.get("relations", []), "relations")
    )
    tombstones = tuple(
        _tombstone(_mapping(item, "tombstone"), source)
        for item in _sequence(data.get("tombstones", []), "tombstones")
    )
    return SyncBatch(
        batch_id=_required_string(data, "batch_id"),
        sequence=_required_int(data, "sequence"),
        run=run,
        records=records,
        relations=relations,
        tombstones=tombstones,
    )


def _record(data: Mapping[str, Any], source: SourceRef) -> Record:
    _reject_unknown(
        data,
        {
            "external_id",
            "external_version",
            "modality",
            "payload",
            "scope",
            "observed_at",
            "occurred_at",
            "explicit_tags",
            "metadata",
        },
        "record",
    )
    payload_data = _mapping(data.get("payload"), "payload")
    _reject_unknown(
        payload_data,
        {"inline", "reference_uri", "content_hash", "media_type"},
        "record payload",
    )
    return Record(
        ref=RecordRef(
            source=source,
            external_id=_required_string(data, "external_id"),
            external_version=_optional_string(data, "external_version"),
        ),
        modality=_enum(RecordModality, data.get("modality"), "record modality"),
        payload=RecordPayload(
            inline=_optional_string(payload_data, "inline"),
            reference_uri=_optional_string(payload_data, "reference_uri"),
            content_hash=_optional_string(payload_data, "content_hash"),
            media_type=_optional_string(payload_data, "media_type"),
        ),
        scope=_scope(_mapping(data.get("scope"), "scope")),
        observed_at=_required_datetime(data, "observed_at"),
        occurred_at=_optional_datetime(data, "occurred_at"),
        explicit_tags=_strings(data.get("explicit_tags", []), "explicit_tags"),
        metadata=_metadata(data),
    )


def _relation(data: Mapping[str, Any], source: SourceRef) -> Relation:
    _reject_unknown(
        data,
        {
            "relation_id",
            "relation_version",
            "relation_type",
            "source",
            "target",
            "scope",
            "observed_at",
            "occurred_at",
            "confidence",
            "metadata",
        },
        "relation",
    )
    confidence = data.get("confidence", 1.0)
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ValueError("confidence must be a number")
    return Relation(
        relation_id=_required_string(data, "relation_id"),
        relation_version=_required_string(data, "relation_version"),
        relation_type=_required_string(data, "relation_type"),
        source=_record_ref(_mapping(data.get("source"), "relation source"), source),
        target=_record_ref(_mapping(data.get("target"), "relation target"), source),
        scope=_scope(_mapping(data.get("scope"), "scope")),
        observed_at=_required_datetime(data, "observed_at"),
        occurred_at=_optional_datetime(data, "occurred_at"),
        confidence=float(confidence),
        metadata=_metadata(data),
    )


def _tombstone(data: Mapping[str, Any], source: SourceRef) -> Tombstone:
    _reject_unknown(
        data,
        {"external_id", "external_version", "tombstone_version", "observed_at", "reason"},
        "tombstone",
    )
    return Tombstone(
        record=RecordRef(
            source=source,
            external_id=_required_string(data, "external_id"),
            external_version=_optional_string(data, "external_version"),
        ),
        tombstone_version=_required_string(data, "tombstone_version"),
        observed_at=_required_datetime(data, "observed_at"),
        reason=_required_string(data, "reason"),
    )


def query_from_mapping(value: object) -> Query:
    data = _mapping(value, "query request")
    _reject_unknown(
        data,
        {
            "request_id",
            "query",
            "scope",
            "top_k",
            "budget_tokens",
            "timeline_id",
            "temporal_mode",
            "as_of",
            "range_start",
            "range_end",
            "reference_time",
            "metadata",
        },
        "query request",
    )
    top_k = _optional_int(data, "top_k")
    return Query(
        request_id=_required_string(data, "request_id"),
        query=_required_string(data, "query"),
        scope=_scope(_mapping(data.get("scope"), "scope")),
        top_k=10 if top_k is None else top_k,
        budget_tokens=_optional_int(data, "budget_tokens"),
        timeline_id=_optional_string(data, "timeline_id"),
        temporal_mode=_enum(
            TemporalQueryMode,
            data.get("temporal_mode", TemporalQueryMode.AUTO.value),
            "temporal_mode",
        ),
        as_of=_optional_datetime(data, "as_of"),
        range_start=_optional_datetime(data, "range_start"),
        range_end=_optional_datetime(data, "range_end"),
        reference_time=_optional_datetime(data, "reference_time"),
        metadata=_metadata(data),
    )


def context_pack_from_mapping(value: object) -> ContextPack:
    data = _mapping(value, "context pack")
    _reject_unknown(
        data,
        {
            "retrieval_id",
            "query_request_id",
            "items",
            "low_confidence",
            "budget_tokens",
            "used_tokens",
            "abstention_reason",
        },
        "context pack",
    )
    items = tuple(
        _evidence_result(_mapping(item, "evidence result"))
        for item in _sequence(data.get("items", []), "items")
    )
    return ContextPack(
        retrieval_id=_required_string(data, "retrieval_id"),
        query_request_id=_required_string(data, "query_request_id"),
        items=items,
        low_confidence=_required_bool(data, "low_confidence"),
        budget_tokens=_optional_int(data, "budget_tokens"),
        used_tokens=_optional_int(data, "used_tokens"),
        abstention_reason=_optional_string(data, "abstention_reason"),
    )


def _evidence_result(data: Mapping[str, Any]) -> EvidenceResult:
    _reject_unknown(
        data,
        {
            "evidence_id",
            "record",
            "modality",
            "content",
            "scope",
            "score",
            "score_evidence",
            "lineage_evidence_ids",
            "metadata",
        },
        "evidence result",
    )
    score = data.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)):
        raise ValueError("score must be a number")
    return EvidenceResult(
        evidence_id=_required_string(data, "evidence_id"),
        record=_record_ref(_mapping(data.get("record"), "record")),
        modality=_enum(RecordModality, data.get("modality"), "record modality"),
        content=_required_string(data, "content"),
        scope=_scope(_mapping(data.get("scope"), "scope")),
        score=float(score),
        score_evidence=_strings(data.get("score_evidence", []), "score_evidence"),
        lineage_evidence_ids=_strings(data.get("lineage_evidence_ids", []), "lineage_evidence_ids"),
        metadata=_metadata(data),
    )


def outcome_from_mapping(value: object) -> Outcome:
    data = _mapping(value, "outcome report")
    _reject_unknown(
        data,
        {
            "request_id",
            "retrieval_id",
            "used_evidence_ids",
            "outcome",
            "occurred_at",
            "reason",
        },
        "outcome report",
    )
    return Outcome(
        request_id=_required_string(data, "request_id"),
        retrieval_id=_required_string(data, "retrieval_id"),
        used_evidence_ids=_strings(data.get("used_evidence_ids"), "used_evidence_ids"),
        outcome=_enum(OutcomeValue, data.get("outcome"), "outcome"),
        occurred_at=_required_datetime(data, "occurred_at"),
        reason=_optional_string(data, "reason") or "",
    )


def sync_batch_acknowledgement_from_mapping(value: object) -> SyncBatchAcknowledgement:
    data = _mapping(value, "sync batch acknowledgement")
    _reject_unknown(
        data,
        {
            "run_request_id",
            "batch_id",
            "sequence",
            "acknowledged_at",
            "accepted_records",
            "accepted_relations",
            "accepted_tombstones",
            "failures",
        },
        "sync batch acknowledgement",
    )
    failures = tuple(
        _sync_item_failure(_mapping(item, "sync item failure"))
        for item in _sequence(data.get("failures", []), "failures")
    )
    return SyncBatchAcknowledgement(
        run_request_id=_required_string(data, "run_request_id"),
        batch_id=_required_string(data, "batch_id"),
        sequence=_required_int(data, "sequence"),
        acknowledged_at=_required_datetime(data, "acknowledged_at"),
        accepted_records=_required_int(data, "accepted_records"),
        accepted_relations=_required_int(data, "accepted_relations"),
        accepted_tombstones=_required_int(data, "accepted_tombstones"),
        failures=failures,
    )


def _sync_item_failure(data: Mapping[str, Any]) -> SyncItemFailure:
    _reject_unknown(
        data,
        {"item_type", "item_id", "item_version", "code", "message", "retryable"},
        "sync item failure",
    )
    return SyncItemFailure(
        item_type=_enum(SyncItemType, data.get("item_type"), "sync item type"),
        item_id=_required_string(data, "item_id"),
        item_version=_required_string(data, "item_version"),
        code=_required_string(data, "code"),
        message=_required_string(data, "message"),
        retryable=_required_bool(data, "retryable"),
    )


def sync_commit_acknowledgement_from_mapping(value: object) -> SyncCommitAcknowledgement:
    data = _mapping(value, "sync commit acknowledgement")
    _reject_unknown(
        data,
        {"request_id", "run_request_id", "source", "committed_cursor", "committed_at"},
        "sync commit acknowledgement",
    )
    return SyncCommitAcknowledgement(
        request_id=_required_string(data, "request_id"),
        run_request_id=_required_string(data, "run_request_id"),
        source=_source_ref(_mapping(data.get("source"), "source")),
        committed_cursor=_required_string(data, "committed_cursor"),
        committed_at=_required_datetime(data, "committed_at"),
    )

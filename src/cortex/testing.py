from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from data_retrieval.connectors.codec import (
    context_pack_from_mapping,
    outcome_from_mapping,
    query_from_mapping,
    source_from_mapping,
    source_to_mapping,
    sync_batch_acknowledgement_from_mapping,
    sync_batch_from_mapping,
    sync_batch_to_mapping,
    sync_commit_acknowledgement_from_mapping,
)
from data_retrieval.connectors.contracts import (
    ConnectorCapability,
    Source,
    SyncBatch,
    SyncRun,
)


@dataclass(frozen=True, slots=True)
class ConnectorContractReport:
    source: Source
    run_count: int
    batch_count: int
    record_count: int
    relation_count: int
    tombstone_count: int


def validate_connector_fixture(value: object) -> ConnectorContractReport:
    """Decode and validate one standalone connector fixture."""

    if not isinstance(value, Mapping) or any(not isinstance(key, str) for key in value):
        raise ValueError("connector fixture must be an object with string keys")
    allowed = {
        "fixture_id",
        "registration",
        "sync_batch",
        "sync_batches",
        "sync_batch_acknowledgement",
        "sync_commit_acknowledgement",
        "query",
        "context_pack",
        "outcome",
    }
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ValueError(f"connector fixture contains unknown fields: {', '.join(unknown)}")
    if "sync_batch" in value and "sync_batches" in value:
        raise ValueError("connector fixture must use sync_batch or sync_batches, not both")
    source = source_from_mapping(value.get("registration"))
    if "sync_batch" in value:
        batches = (sync_batch_from_mapping(value["sync_batch"]),)
    else:
        raw_batches = value.get("sync_batches")
        if isinstance(raw_batches, (str, bytes)) or not isinstance(raw_batches, Sequence):
            raise ValueError("connector fixture sync_batches must be an array")
        batches = tuple(sync_batch_from_mapping(batch) for batch in raw_batches)
    report = validate_source_sync(source, batches)
    batch_keys = {
        (batch.run.request_id, batch.batch_id, batch.sequence) for batch in batches
    }
    if "sync_batch_acknowledgement" in value:
        acknowledgement = sync_batch_acknowledgement_from_mapping(
            value["sync_batch_acknowledgement"]
        )
        if (
            acknowledgement.run_request_id,
            acknowledgement.batch_id,
            acknowledgement.sequence,
        ) not in batch_keys:
            raise ValueError("fixture batch acknowledgement does not match a sync batch")
    if "sync_commit_acknowledgement" in value:
        commit = sync_commit_acknowledgement_from_mapping(
            value["sync_commit_acknowledgement"]
        )
        if commit.run_request_id not in {batch.run.request_id for batch in batches}:
            raise ValueError("fixture commit acknowledgement does not match a sync run")
        if commit.source != source.source:
            raise ValueError("fixture commit acknowledgement source does not match registration")

    query = query_from_mapping(value["query"]) if "query" in value else None
    context = (
        context_pack_from_mapping(value["context_pack"])
        if "context_pack" in value
        else None
    )
    outcome = outcome_from_mapping(value["outcome"]) if "outcome" in value else None
    if query is not None and context is not None:
        if context.query_request_id != query.request_id:
            raise ValueError("fixture context pack does not match its query")
    if context is not None and outcome is not None:
        if outcome.retrieval_id != context.retrieval_id:
            raise ValueError("fixture outcome does not match its context pack")
        evidence_ids = {item.evidence_id for item in context.items}
        if not set(outcome.used_evidence_ids).issubset(evidence_ids):
            raise ValueError("fixture outcome uses evidence outside its context pack")
    return report


def validate_source_sync(
    source: Source, batches: Sequence[SyncBatch]
) -> ConnectorContractReport:
    """Validate connector-owned mapping output without a Cortex repository or server."""

    if ConnectorCapability.SOURCE_SYNC not in source.capabilities:
        raise ValueError("connector fixture source must declare source_sync")
    if not batches:
        raise ValueError("connector fixture requires at least one sync batch")
    if source_from_mapping(source_to_mapping(source)) != source:
        raise ValueError("connector source does not round-trip through the public codec")

    runs: dict[str, SyncRun] = {}
    sequences: defaultdict[str, list[int]] = defaultdict(list)
    batch_keys: set[tuple[str, str, int]] = set()
    for batch in batches:
        if batch.run.source != source.source:
            raise ValueError("connector batch source does not match registration")
        existing_run = runs.setdefault(batch.run.request_id, batch.run)
        if existing_run != batch.run:
            raise ValueError("connector run request identity has conflicting payloads")
        key = (batch.run.request_id, batch.batch_id, batch.sequence)
        if key in batch_keys:
            raise ValueError("connector fixture contains a duplicate batch identity")
        batch_keys.add(key)
        sequences[batch.run.request_id].append(batch.sequence)
        if sync_batch_from_mapping(sync_batch_to_mapping(batch)) != batch:
            raise ValueError("connector batch does not round-trip through the public codec")

    for request_id, values in sequences.items():
        ordered = sorted(values)
        if ordered != list(range(len(ordered))):
            raise ValueError(
                f"connector run {request_id} batch sequences must be contiguous from zero"
            )

    return ConnectorContractReport(
        source=source,
        run_count=len(runs),
        batch_count=len(batches),
        record_count=sum(len(batch.records) for batch in batches),
        relation_count=sum(len(batch.relations) for batch in batches),
        tombstone_count=sum(len(batch.tombstones) for batch in batches),
    )

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import (
    AtomLink,
    AtomTag,
    CalibrationSignal,
    CalibrationTarget,
    TagRelation,
    WeightEvent,
    WeightEventSource,
)

WeightedEdge = AtomTag | AtomLink | TagRelation
EdgeCoordinates = tuple[CalibrationTarget, str, str, str]


def edge_coordinates(edge: WeightedEdge) -> EdgeCoordinates:
    if isinstance(edge, AtomTag):
        return CalibrationTarget.ATOM_TAG, edge.atom_id, edge.tag_id, "has_tag"
    if isinstance(edge, AtomLink):
        return (
            CalibrationTarget.ATOM_LINK,
            edge.from_atom_id,
            edge.to_atom_id,
            edge.relation.value,
        )
    return (
        CalibrationTarget.TAG_RELATION,
        edge.source_tag_id,
        edge.target_tag_id,
        edge.relation_type,
    )


def transition_event(
    *,
    namespace: str,
    edge: WeightedEdge,
    weight_before: float,
    source_type: WeightEventSource,
    source_id: str,
    policy_version: str,
    created_at: datetime,
    metadata: dict[str, Any] | None = None,
) -> WeightEvent | None:
    weight_after = edge.weight_raw
    if abs(weight_after - weight_before) <= 1e-12:
        return None
    target_type, target_id, related_id, relation_type = edge_coordinates(edge)
    event_id = stable_id(
        "weight-event",
        namespace,
        target_type.value,
        target_id,
        related_id,
        relation_type,
        source_type.value,
        source_id,
        format(weight_before, ".17g"),
        format(weight_after, ".17g"),
    )
    return WeightEvent(
        event_id=event_id,
        namespace=namespace,
        target_type=target_type,
        target_id=target_id,
        related_id=related_id,
        relation_type=relation_type,
        source_type=source_type,
        source_id=source_id,
        policy_version=policy_version,
        weight_before=weight_before,
        weight_after=weight_after,
        delta=weight_after - weight_before,
        created_at=created_at,
        metadata=dict(metadata or {}),
    )


def transition_events(
    *,
    namespace: str,
    edges: Iterable[WeightedEdge],
    previous_weights: Mapping[EdgeCoordinates, float],
    source_type: WeightEventSource,
    source_id: str,
    policy_version: str,
    metadata: dict[str, Any] | None = None,
) -> tuple[WeightEvent, ...]:
    events: list[WeightEvent] = []
    for edge in edges:
        coordinates = edge_coordinates(edge)
        event = transition_event(
            namespace=namespace,
            edge=edge,
            weight_before=previous_weights.get(coordinates, 0.0),
            source_type=source_type,
            source_id=source_id,
            policy_version=policy_version,
            created_at=edge.updated_at,
            metadata=metadata,
        )
        if event is not None:
            events.append(event)
    return tuple(events)


def calibration_transition_events(
    *,
    namespace: str,
    edges: Iterable[WeightedEdge],
    previous_weights: Mapping[EdgeCoordinates, float],
    signals: tuple[CalibrationSignal, ...],
) -> tuple[WeightEvent, ...]:
    events: list[WeightEvent] = []
    for edge in edges:
        coordinates = edge_coordinates(edge)
        relevant = tuple(
            signal
            for signal in signals
            if _signal_matches(signal, coordinates)
        )
        if not relevant:
            if abs(edge.weight_raw - previous_weights.get(coordinates, 0.0)) > 1e-12:
                raise ValueError(
                    "calibration changed an edge without a matching calibration signal: "
                    f"{coordinates}"
                )
            continue
        signal_ids = tuple(sorted(signal.signal_id for signal in relevant))
        source_id = (
            signal_ids[0]
            if len(signal_ids) == 1
            else stable_id("calibration-weight-group", *signal_ids)
        )
        profiles = tuple(
            sorted({f"{signal.provider}:{signal.profile_version}" for signal in relevant})
        )
        event = transition_event(
            namespace=namespace,
            edge=edge,
            weight_before=previous_weights.get(coordinates, 0.0),
            source_type=WeightEventSource.CALIBRATION,
            source_id=source_id,
            policy_version="+".join(profiles),
            created_at=edge.updated_at,
            metadata={"signal_ids": signal_ids} if len(signal_ids) > 1 else {},
        )
        if event is not None:
            events.append(event)
    return tuple(events)


def _signal_matches(
    signal: CalibrationSignal,
    coordinates: EdgeCoordinates,
) -> bool:
    target_type, target_id, related_id, relation_type = coordinates
    if signal.target_type is not target_type:
        return False
    if signal.target_id != target_id or signal.related_id != related_id:
        return False
    if target_type is CalibrationTarget.ATOM_TAG:
        return True
    return signal.relation_type == relation_type

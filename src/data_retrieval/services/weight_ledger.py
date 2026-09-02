from __future__ import annotations

from dataclasses import dataclass, replace

from data_retrieval.core.weight_events import EdgeCoordinates, edge_coordinates
from data_retrieval.domain.models import (
    AtomLink,
    AtomTag,
    CalibrationTarget,
    TagRelation,
    WeightEvent,
    utc_now,
)
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class TargetWeightAudit:
    target_type: CalibrationTarget
    target_id: str
    related_id: str
    relation_type: str
    event_count: int
    reconstructed_weight: float | None
    aggregate_weight: float | None
    valid: bool
    issues: tuple[str, ...]
    source_types: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class NamespaceWeightAudit:
    namespace: str
    target_count: int
    event_count: int
    valid_target_count: int
    mismatched_target_count: int
    missing_event_target_count: int
    targets: tuple[TargetWeightAudit, ...]

    @property
    def passed(self) -> bool:
        return self.valid_target_count == self.target_count


class WeightLedgerService:
    """Reconstruct and, when explicitly requested, repair serving weight aggregates."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def audit_namespace(self, namespace: str) -> NamespaceWeightAudit:
        edges = self._current_edges(namespace)
        current = {edge_coordinates(edge): edge for edge in edges}
        events = self.repository.list_weight_events(namespace=namespace)
        by_target: dict[EdgeCoordinates, list[WeightEvent]] = {}
        for event in events:
            by_target.setdefault(self._event_coordinates(event), []).append(event)
        keys = tuple(sorted(set(current) | set(by_target), key=self._sort_key))
        audits = tuple(
            self._audit_target(
                key,
                tuple(by_target.get(key, ())),
                current.get(key),
            )
            for key in keys
        )
        return NamespaceWeightAudit(
            namespace=namespace,
            target_count=len(audits),
            event_count=len(events),
            valid_target_count=sum(audit.valid for audit in audits),
            mismatched_target_count=sum(
                "aggregate_mismatch" in audit.issues for audit in audits
            ),
            missing_event_target_count=sum("missing_events" in audit.issues for audit in audits),
            targets=audits,
        )

    def repair_aggregates(self, namespace: str) -> NamespaceWeightAudit:
        audit = self.audit_namespace(namespace)
        invalid_chains = tuple(
            target
            for target in audit.targets
            if any(issue != "aggregate_mismatch" for issue in target.issues)
        )
        if invalid_chains:
            first = invalid_chains[0]
            raise ValueError(
                "cannot repair aggregates while the ledger has structural issues: "
                f"{first.target_type.value}:{first.target_id}:{first.related_id}:"
                f"{first.relation_type} ({', '.join(first.issues)})"
            )
        current = {
            edge_coordinates(edge): edge for edge in self._current_edges(namespace)
        }
        atom_tags: list[AtomTag] = []
        atom_links: list[AtomLink] = []
        tag_relations: list[TagRelation] = []
        now = utc_now()
        for target in audit.targets:
            if "aggregate_mismatch" not in target.issues:
                continue
            key = (
                target.target_type,
                target.target_id,
                target.related_id,
                target.relation_type,
            )
            edge = current[key]
            assert target.reconstructed_weight is not None
            repaired = replace(
                edge,
                weight_raw=target.reconstructed_weight,
                updated_at=now,
            )
            if isinstance(repaired, AtomTag):
                atom_tags.append(repaired)
            elif isinstance(repaired, AtomLink):
                atom_links.append(repaired)
            else:
                tag_relations.append(repaired)
        self.repository.restore_weight_aggregates(
            atom_tags=tuple(atom_tags),
            atom_links=tuple(atom_links),
            tag_relations=tuple(tag_relations),
        )
        return self.audit_namespace(namespace)

    def _audit_target(
        self,
        key: EdgeCoordinates,
        events: tuple[WeightEvent, ...],
        edge: AtomTag | AtomLink | TagRelation | None,
    ) -> TargetWeightAudit:
        issues: list[str] = []
        reconstructed: float | None = None
        if not events:
            issues.append("missing_events")
        else:
            reconstructed, chain_issues = self._reconstruct(events)
            issues.extend(chain_issues)
        aggregate = edge.weight_raw if edge is not None else None
        if edge is None:
            issues.append("missing_aggregate")
        elif reconstructed is not None and abs(reconstructed - aggregate) > 1e-9:
            issues.append("aggregate_mismatch")
        return TargetWeightAudit(
            target_type=key[0],
            target_id=key[1],
            related_id=key[2],
            relation_type=key[3],
            event_count=len(events),
            reconstructed_weight=reconstructed,
            aggregate_weight=aggregate,
            valid=not issues,
            issues=tuple(issues),
            source_types=tuple(sorted({event.source_type.value for event in events})),
        )

    @staticmethod
    def _reconstruct(events: tuple[WeightEvent, ...]) -> tuple[float, tuple[str, ...]]:
        ordered = sorted(events, key=lambda event: (event.created_at, event.event_id))
        current = 0.0
        issues: list[str] = []
        for event in ordered:
            if abs(event.delta - (event.weight_after - event.weight_before)) > 1e-9:
                issues.append("invalid_event_delta")
                break
            if abs(event.weight_before - current) > 1e-9:
                issues.append("broken_event_chain")
                break
            current = event.weight_after
        if issues and len(ordered) > 1:
            issues.append("incomplete_event_chain")
        return current, tuple(issues)

    def _current_edges(
        self, namespace: str
    ) -> tuple[AtomTag | AtomLink | TagRelation, ...]:
        return (
            *self.repository.list_atom_tags(namespace),
            *self.repository.list_atom_links(namespace=namespace),
            *self.repository.list_tag_relations(namespace=namespace),
        )

    @staticmethod
    def _event_coordinates(event: WeightEvent) -> EdgeCoordinates:
        return (
            event.target_type,
            event.target_id,
            event.related_id,
            event.relation_type,
        )

    @staticmethod
    def _sort_key(key: EdgeCoordinates) -> tuple[str, str, str, str]:
        return key[0].value, key[1], key[2], key[3]

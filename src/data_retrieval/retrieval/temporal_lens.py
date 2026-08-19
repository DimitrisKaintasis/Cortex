from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from data_retrieval.domain.models import Atom, AtomKind, AtomLink, AtomLinkRelation
from data_retrieval.retrieval.models import QueryPlan, TemporalMode


@dataclass(frozen=True, slots=True)
class TemporalAssessment:
    eligible_atom_ids: frozenset[str]
    superseded_atom_ids: frozenset[str]
    temporal_scores: dict[str, float]
    roles: dict[str, str]
    resolved_mode: TemporalMode


class TemporalLens:
    """Apply time semantics after relevance candidate discovery."""

    def assess(
        self,
        *,
        plan: QueryPlan,
        atoms: tuple[Atom, ...],
        links: tuple[AtomLink, ...],
        auto_detect_state: bool,
    ) -> TemporalAssessment:
        scoped = tuple(
            atom
            for atom in atoms
            if plan.timeline_id is None
            or atom.metadata.get("timeline_id", plan.timeline_id) == plan.timeline_id
        )
        mode = plan.temporal_mode
        supersedes = tuple(link for link in links if link.relation is AtomLinkRelation.SUPERSEDES)
        repeated_state = self._has_repeated_state(scoped)
        if auto_detect_state and mode is TemporalMode.NONE and (supersedes or repeated_state):
            mode = TemporalMode.CURRENT_STATE

        eligible = {
            atom.atom_id for atom in scoped if self._eligible(atom=atom, plan=plan, mode=mode)
        }
        superseded = {
            link.to_atom_id
            for link in supersedes
            if link.from_atom_id in eligible and link.to_atom_id in eligible
        }
        temporal_scores: dict[str, float] = {}
        roles: dict[str, str] = {}

        if mode in {TemporalMode.CURRENT_STATE, TemporalMode.AS_OF}:
            state_groups = self._state_groups(scoped, eligible)
            for group in state_groups.values():
                ordered = sorted(group, key=self._time_key, reverse=True)
                for index, atom in enumerate(ordered):
                    if atom.atom_id in superseded:
                        roles[atom.atom_id] = "historical_superseded"
                        continue
                    temporal_scores[atom.atom_id] = 0.15 if index == 0 else 0.05
                    roles[atom.atom_id] = "current_state" if index == 0 else "state_evidence"
            eligible.difference_update(superseded)
        elif mode in {TemporalMode.HISTORY, TemporalMode.RANGE}:
            for atom in scoped:
                if atom.atom_id not in eligible:
                    continue
                if atom.kind is AtomKind.TEMPORAL_SUMMARY:
                    temporal_scores[atom.atom_id] = 0.10
                    roles[atom.atom_id] = "continuity"
                else:
                    temporal_scores[atom.atom_id] = 0.04
                    roles[atom.atom_id] = "source_evidence"

        return TemporalAssessment(
            eligible_atom_ids=frozenset(eligible),
            superseded_atom_ids=frozenset(superseded),
            temporal_scores=temporal_scores,
            roles=roles,
            resolved_mode=mode,
        )

    def _eligible(self, *, atom: Atom, plan: QueryPlan, mode: TemporalMode) -> bool:
        if mode in {TemporalMode.AS_OF, TemporalMode.CURRENT_STATE} and plan.as_of:
            return self._effective_time(atom) <= plan.as_of
        if mode is TemporalMode.RANGE and plan.range_start and plan.range_end:
            if atom.kind is AtomKind.TEMPORAL_SUMMARY:
                period_start = self._metadata_time(atom.metadata.get("period_start"))
                period_end = self._metadata_time(atom.metadata.get("period_end"))
                if period_start and period_end:
                    return period_start < plan.range_end and period_end > plan.range_start
            return (
                atom.occurred_at is not None
                and plan.range_start <= atom.occurred_at < plan.range_end
            )
        return True

    @staticmethod
    def _state_groups(atoms: tuple[Atom, ...], eligible: set[str]) -> dict[str, list[Atom]]:
        groups: dict[str, list[Atom]] = {}
        for atom in atoms:
            if atom.atom_id not in eligible:
                continue
            state_key = atom.metadata.get("state_key")
            if state_key:
                groups.setdefault(str(state_key), []).append(atom)
        return groups

    @staticmethod
    def _has_repeated_state(atoms: tuple[Atom, ...]) -> bool:
        seen: set[str] = set()
        for atom in atoms:
            state_key = atom.metadata.get("state_key")
            if not state_key:
                continue
            normalized = str(state_key)
            if normalized in seen:
                return True
            seen.add(normalized)
        return False

    @staticmethod
    def _effective_time(atom: Atom) -> datetime:
        return atom.occurred_at or atom.created_at

    @classmethod
    def _time_key(cls, atom: Atom) -> tuple[datetime, str]:
        return cls._effective_time(atom), atom.atom_id

    @staticmethod
    def _metadata_time(value: Any) -> datetime | None:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value)
            except ValueError:
                return None
        return None

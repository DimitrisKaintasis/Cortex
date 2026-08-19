from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations

from data_retrieval.domain.models import (
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    AtomTag,
    TagRelation,
    utc_now,
)
from data_retrieval.retrieval.models import FeedbackRequest
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class FeedbackResult:
    feedback_id: str
    credited_atom_ids: tuple[str, ...]
    atom_tag_updates: int
    atom_link_updates: int
    tag_relation_updates: int


class LearningService:
    """Turn explicit retrieval outcomes into small, auditable weight updates."""

    def __init__(
        self,
        repository: Repository,
        *,
        atom_tag_step: float = 0.05,
        relationship_step: float = 0.10,
        summary_credit: float = 0.25,
        maximum_weight: float = 10.0,
        maximum_relationship_nodes: int = 12,
    ) -> None:
        if maximum_relationship_nodes < 2:
            raise ValueError("maximum_relationship_nodes must be at least two")
        self.repository = repository
        self.atom_tag_step = atom_tag_step
        self.relationship_step = relationship_step
        self.summary_credit = summary_credit
        self.maximum_weight = maximum_weight
        self.maximum_relationship_nodes = maximum_relationship_nodes

    def apply_feedback(self, request: FeedbackRequest) -> FeedbackResult:
        event = self.repository.get_retrieval_event(request.retrieval_id)
        if event is None:
            raise ValueError(f"unknown retrieval_id: {request.retrieval_id}")
        returned_ids = {str(value) for value in event.get("returned_atom_ids", [])}
        selected_ids = set(request.selected_atom_ids)
        if not selected_ids.issubset(returned_ids):
            raise ValueError("feedback can only select atoms returned by this retrieval")

        namespace = str(event["namespace"])
        sign = 1.0 if request.outcome == "positive" else -1.0
        credit = self._source_credit(namespace, selected_ids)
        tags = self.repository.list_tags(namespace)
        tags_by_id = {tag.tag_id: tag for tag in tags}
        query_tags = {str(value) for value in event.get("query_tags", [])}

        atom_tag_updates: list[AtomTag] = []
        credited_tag_ids: set[str] = set()
        for atom_id, factor in sorted(credit.items()):
            for edge in self.repository.atom_tags_for(atom_id):
                tag = tags_by_id.get(edge.tag_id)
                if tag is None:
                    continue
                credited_tag_ids.add(edge.tag_id)
                if tag.canonical_text not in query_tags:
                    continue
                atom_tag_updates.append(
                    replace(
                        edge,
                        weight_raw=self._bounded(
                            edge.weight_raw + sign * self.atom_tag_step * factor
                        ),
                        evidence_sources=self._with_evidence(
                            edge.evidence_sources, request.feedback_id
                        ),
                        updated_at=utc_now(),
                    )
                )

        atom_link_updates = self._co_used_updates(
            namespace=namespace,
            atom_ids=tuple(sorted(credit))[: self.maximum_relationship_nodes],
            sign=sign,
            feedback_id=request.feedback_id,
        )
        query_tag_ids = {tag.tag_id for tag in tags if tag.canonical_text in query_tags}
        relationship_tag_ids = [*sorted(query_tag_ids)]
        relationship_tag_ids.extend(
            tag_id for tag_id in sorted(credited_tag_ids) if tag_id not in query_tag_ids
        )
        tag_relation_updates = self._tag_relation_updates(
            namespace=namespace,
            tag_ids=tuple(relationship_tag_ids[: self.maximum_relationship_nodes]),
            sign=sign,
            feedback_id=request.feedback_id,
        )
        now = utc_now()
        feedback_event: dict[str, object] = {
            "feedback_id": request.feedback_id,
            "retrieval_id": request.retrieval_id,
            "namespace": namespace,
            "outcome": request.outcome,
            "reason": request.reason,
            "selected_atom_ids": sorted(selected_ids),
            "credited_atom_ids": sorted(credit),
            "created_at": now.isoformat(),
        }
        self.repository.apply_learning_updates(
            feedback_event=feedback_event,
            atom_tags=tuple(atom_tag_updates),
            atom_links=atom_link_updates,
            tag_relations=tag_relation_updates,
        )
        return FeedbackResult(
            feedback_id=request.feedback_id,
            credited_atom_ids=tuple(sorted(credit)),
            atom_tag_updates=len(atom_tag_updates),
            atom_link_updates=len(atom_link_updates),
            tag_relation_updates=len(tag_relation_updates),
        )

    def _source_credit(self, namespace: str, selected_ids: set[str]) -> dict[str, float]:
        links = self.repository.list_atom_links(namespace=namespace)
        children: dict[str, set[str]] = {}
        for link in links:
            if link.relation in {
                AtomLinkRelation.SUMMARIZES,
                AtomLinkRelation.DERIVED_FROM,
            }:
                children.setdefault(link.from_atom_id, set()).add(link.to_atom_id)

        credit: dict[str, float] = {}
        for selected_id in selected_ids:
            atom = self.repository.get_atom(selected_id)
            if atom is None:
                continue
            if atom.kind is AtomKind.SOURCE:
                credit[selected_id] = 1.0
                continue
            stack = list(children.get(selected_id, ()))
            visited: set[str] = set()
            while stack:
                atom_id = stack.pop()
                if atom_id in visited:
                    continue
                visited.add(atom_id)
                descendant = self.repository.get_atom(atom_id)
                if descendant is not None and descendant.kind is AtomKind.SOURCE:
                    credit[atom_id] = max(credit.get(atom_id, 0.0), self.summary_credit)
                else:
                    stack.extend(children.get(atom_id, ()))
        return credit

    def _co_used_updates(
        self,
        *,
        namespace: str,
        atom_ids: tuple[str, ...],
        sign: float,
        feedback_id: str,
    ) -> tuple[AtomLink, ...]:
        existing = {
            (link.from_atom_id, link.to_atom_id): link
            for link in self.repository.list_atom_links(
                namespace=namespace, relation=AtomLinkRelation.CO_USED
            )
        }
        updates: list[AtomLink] = []
        for left, right in combinations(atom_ids, 2):
            key = tuple(sorted((left, right)))
            edge = existing.get(key)
            if edge is None:
                if sign < 0:
                    continue
                updates.append(
                    AtomLink(
                        from_atom_id=key[0],
                        to_atom_id=key[1],
                        relation=AtomLinkRelation.CO_USED,
                        weight_raw=self.relationship_step,
                        evidence_sources=(feedback_id,),
                        metadata={"learned": True},
                    )
                )
                continue
            updates.append(
                replace(
                    edge,
                    weight_raw=self._bounded(edge.weight_raw + sign * self.relationship_step),
                    evidence_sources=self._with_evidence(edge.evidence_sources, feedback_id),
                    updated_at=utc_now(),
                )
            )
        return tuple(updates)

    def _tag_relation_updates(
        self,
        *,
        namespace: str,
        tag_ids: tuple[str, ...],
        sign: float,
        feedback_id: str,
    ) -> tuple[TagRelation, ...]:
        existing = {
            (edge.source_tag_id, edge.target_tag_id): edge
            for edge in self.repository.list_tag_relations(
                namespace=namespace, relation_type="co_occurs"
            )
        }
        updates: list[TagRelation] = []
        for left, right in combinations(tag_ids, 2):
            key = tuple(sorted((left, right)))
            edge = existing.get(key)
            if edge is None:
                if sign < 0:
                    continue
                updates.append(
                    TagRelation(
                        source_tag_id=key[0],
                        target_tag_id=key[1],
                        relation_type="co_occurs",
                        weight_raw=self.relationship_step,
                        confidence=1.0,
                        evidence_sources=(feedback_id,),
                    )
                )
                continue
            updates.append(
                replace(
                    edge,
                    weight_raw=self._bounded(edge.weight_raw + sign * self.relationship_step),
                    evidence_sources=self._with_evidence(edge.evidence_sources, feedback_id),
                    updated_at=utc_now(),
                )
            )
        return tuple(updates)

    def _bounded(self, value: float) -> float:
        return min(self.maximum_weight, max(0.0, value))

    @staticmethod
    def _with_evidence(existing: tuple[str, ...], feedback_id: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*existing, feedback_id)))[-50:]

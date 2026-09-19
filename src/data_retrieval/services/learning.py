from __future__ import annotations

from dataclasses import dataclass, replace
from itertools import combinations

from data_retrieval.domain.models import (
    AtomLink,
    AtomLinkRelation,
    AtomRole,
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
    learning_multiplier: float


@dataclass(frozen=True, slots=True)
class LearningPolicy:
    """Versioned switches for the independently testable feedback channels."""

    policy_id: str
    learn_atom_tags: bool = True
    learn_co_used: bool = True
    learn_tag_relations: bool = True
    tag_relation_scope: str = "all_pairs"

    def __post_init__(self) -> None:
        if not self.policy_id.strip():
            raise ValueError("policy_id cannot be empty")
        if self.tag_relation_scope not in {"all_pairs", "query_to_evidence"}:
            raise ValueError("tag_relation_scope must be all_pairs or query_to_evidence")


ALL_PAIRS_LEARNING_POLICY = LearningPolicy(policy_id="bounded-feedback-v1")
ATOM_CO_USED_LEARNING_POLICY = LearningPolicy(
    policy_id="bounded-atom-co-used-v1",
    learn_tag_relations=False,
)
ATOM_TAG_ONLY_LEARNING_POLICY = LearningPolicy(
    policy_id="bounded-atom-tag-only-v1",
    learn_co_used=False,
    learn_tag_relations=False,
)
CO_USED_ONLY_LEARNING_POLICY = LearningPolicy(
    policy_id="bounded-co-used-only-v1",
    learn_atom_tags=False,
    learn_tag_relations=False,
)
QUERY_EVIDENCE_LEARNING_POLICY = LearningPolicy(
    policy_id="bounded-query-evidence-v1",
    tag_relation_scope="query_to_evidence",
)
LEARNING_POLICY_PROFILES = {
    "all_pairs": ALL_PAIRS_LEARNING_POLICY,
    "atom_co_used": ATOM_CO_USED_LEARNING_POLICY,
    "atom_tags_only": ATOM_TAG_ONLY_LEARNING_POLICY,
    "co_used_only": CO_USED_ONLY_LEARNING_POLICY,
    "query_evidence": QUERY_EVIDENCE_LEARNING_POLICY,
}


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
        policy: LearningPolicy = ALL_PAIRS_LEARNING_POLICY,
    ) -> None:
        if maximum_relationship_nodes < 2:
            raise ValueError("maximum_relationship_nodes must be at least two")
        self.repository = repository
        self.atom_tag_step = atom_tag_step
        self.relationship_step = relationship_step
        self.summary_credit = summary_credit
        self.maximum_weight = maximum_weight
        self.maximum_relationship_nodes = maximum_relationship_nodes
        self.policy = policy

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
        learning_multiplier = self._learning_multiplier(request, tuple(credit))
        query_tags = {str(value) for value in event.get("query_tags", [])}

        edges_by_atom = {
            atom_id: self.repository.atom_tags_for(atom_id) for atom_id in credit
        }
        relevant_tag_ids = {
            edge.tag_id for edges in edges_by_atom.values() for edge in edges
        }
        tags_by_id = {
            tag.tag_id: tag
            for tag in self.repository.get_tags(tuple(sorted(relevant_tag_ids)))
        }

        atom_tag_updates: list[AtomTag] = []
        credited_tag_ids: set[str] = set()
        for atom_id, factor in sorted(credit.items()):
            for edge in edges_by_atom[atom_id]:
                tag = tags_by_id.get(edge.tag_id)
                if tag is None:
                    continue
                credited_tag_ids.add(edge.tag_id)
                if not self.policy.learn_atom_tags or tag.canonical_text not in query_tags:
                    continue
                atom_tag_updates.append(
                    replace(
                        edge,
                        weight_raw=self._bounded(
                            edge.weight_raw
                            + sign * self.atom_tag_step * factor * learning_multiplier
                        ),
                        evidence_sources=self._with_evidence(
                            edge.evidence_sources, request.feedback_id
                        ),
                        updated_at=utc_now(),
                    )
                )

        atom_link_updates = (
            self._co_used_updates(
                namespace=namespace,
                atom_ids=tuple(sorted(credit))[: self.maximum_relationship_nodes],
                sign=sign,
                feedback_id=request.feedback_id,
                multiplier=learning_multiplier,
            )
            if self.policy.learn_co_used
            else ()
        )
        tag_relation_updates: tuple[TagRelation, ...] = ()
        if self.policy.learn_tag_relations:
            query_tag_ids = {
                tag.tag_id
                for tag in self.repository.get_tags_by_canonical(
                    namespace=namespace, canonical_texts=tuple(sorted(query_tags))
                )
            }
            if self.policy.tag_relation_scope == "query_to_evidence":
                bounded_tag_ids = [*sorted(query_tag_ids)]
                bounded_tag_ids.extend(
                    tag_id for tag_id in sorted(credited_tag_ids) if tag_id not in query_tag_ids
                )
                allowed_tag_ids = set(
                    bounded_tag_ids[: self.maximum_relationship_nodes]
                )
                pairs = tuple(
                    sorted(
                        {
                            tuple(sorted((query_tag_id, evidence_tag_id)))
                            for query_tag_id in query_tag_ids
                            for evidence_tag_id in credited_tag_ids
                            if query_tag_id != evidence_tag_id
                            and query_tag_id in allowed_tag_ids
                            and evidence_tag_id in allowed_tag_ids
                        }
                    )
                )
                tag_relation_updates = self._tag_relation_pair_updates(
                    namespace=namespace,
                    pairs=pairs,
                    sign=sign,
                    feedback_id=request.feedback_id,
                    multiplier=learning_multiplier,
                )
            else:
                relationship_tag_ids = [*sorted(query_tag_ids)]
                relationship_tag_ids.extend(
                    tag_id for tag_id in sorted(credited_tag_ids) if tag_id not in query_tag_ids
                )
                tag_relation_updates = self._tag_relation_updates(
                    namespace=namespace,
                    tag_ids=tuple(relationship_tag_ids[: self.maximum_relationship_nodes]),
                    sign=sign,
                    feedback_id=request.feedback_id,
                    multiplier=learning_multiplier,
                )
        now = utc_now()
        feedback_event: dict[str, object] = {
            "feedback_id": request.feedback_id,
            "retrieval_id": request.retrieval_id,
            "namespace": namespace,
            "outcome": request.outcome,
            "reason": request.reason,
            "occurred_at": (
                request.occurred_at.isoformat() if request.occurred_at else None
            ),
            "selected_atom_ids": sorted(selected_ids),
            "credited_atom_ids": sorted(credit),
            "used_mem0": request.used_mem0,
            "learning_multiplier": learning_multiplier,
            "policy_version": self.policy.policy_id,
            "learning_channels": {
                "atom_tags": self.policy.learn_atom_tags,
                "co_used": self.policy.learn_co_used,
                "tag_relations": self.policy.learn_tag_relations,
                "tag_relation_scope": self.policy.tag_relation_scope,
            },
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
            learning_multiplier=learning_multiplier,
        )

    def _source_credit(self, namespace: str, selected_ids: set[str]) -> dict[str, float]:
        credit: dict[str, float] = {}
        for selected_id in selected_ids:
            atom = self.repository.get_atom(selected_id)
            if atom is None:
                continue
            if atom.role is AtomRole.SOURCE:
                credit[selected_id] = 1.0
                continue
            stack = list(self._lineage_targets(selected_id))
            visited: set[str] = set()
            credited_source = False
            while stack:
                atom_id = stack.pop()
                if atom_id in visited:
                    continue
                visited.add(atom_id)
                descendant = self.repository.get_atom(atom_id)
                if descendant is not None and descendant.role is AtomRole.SOURCE:
                    credit[atom_id] = max(credit.get(atom_id, 0.0), self.summary_credit)
                    credited_source = True
                else:
                    stack.extend(self._lineage_targets(atom_id))
            if not credited_source:
                # A useful derived artifact without raw lineage must still be able to learn.
                # Its role remains derived; this is feedback credit, not source authority.
                credit[selected_id] = 1.0
        return credit

    def _lineage_targets(self, atom_id: str) -> tuple[str, ...]:
        atom = self.repository.get_atom(atom_id)
        links = self.repository.get_atom_links(atom_id)
        if atom is not None and atom.metadata.get("source_system") == "mem0":
            # Legacy Mem0 DERIVED_FROM links represented whole input batches. Only
            # v2 fact-level support is safe for outcome-credit propagation.
            allowed = {AtomLinkRelation.SUPPORTED_BY}
        else:
            allowed = {
                AtomLinkRelation.SUMMARIZES,
                AtomLinkRelation.DERIVED_FROM,
                AtomLinkRelation.SUPPORTED_BY,
            }
        return tuple(link.to_atom_id for link in links if link.relation in allowed)

    def _co_used_updates(
        self,
        *,
        namespace: str,
        atom_ids: tuple[str, ...],
        sign: float,
        feedback_id: str,
        multiplier: float,
    ) -> tuple[AtomLink, ...]:
        existing = {
            (link.from_atom_id, link.to_atom_id): link
            for link in self.repository.get_atom_links_touching(
                atom_ids=atom_ids, relation=AtomLinkRelation.CO_USED
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
                        weight_raw=self.relationship_step * multiplier,
                        evidence_sources=(feedback_id,),
                        metadata={"learned": True},
                    )
                )
                continue
            updates.append(
                replace(
                    edge,
                    weight_raw=self._bounded(
                        edge.weight_raw + sign * self.relationship_step * multiplier
                    ),
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
        multiplier: float,
    ) -> tuple[TagRelation, ...]:
        return self._tag_relation_pair_updates(
            namespace=namespace,
            pairs=tuple(combinations(tag_ids, 2)),
            sign=sign,
            feedback_id=feedback_id,
            multiplier=multiplier,
        )

    def _tag_relation_pair_updates(
        self,
        *,
        namespace: str,
        pairs: tuple[tuple[str, str], ...],
        sign: float,
        feedback_id: str,
        multiplier: float,
    ) -> tuple[TagRelation, ...]:
        tag_ids = tuple(sorted({tag_id for pair in pairs for tag_id in pair}))
        existing = {
            (edge.source_tag_id, edge.target_tag_id): edge
            for edge in self.repository.get_tag_relations_touching(
                tag_ids=tag_ids, relation_type="co_occurs"
            )
        }
        updates: list[TagRelation] = []
        for left, right in pairs:
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
                        weight_raw=self.relationship_step * multiplier,
                        confidence=1.0,
                        evidence_sources=(feedback_id,),
                    )
                )
                continue
            updates.append(
                replace(
                    edge,
                    weight_raw=self._bounded(
                        edge.weight_raw + sign * self.relationship_step * multiplier
                    ),
                    evidence_sources=self._with_evidence(edge.evidence_sources, feedback_id),
                    updated_at=utc_now(),
                )
            )
        return tuple(updates)

    def _bounded(self, value: float) -> float:
        return min(self.maximum_weight, max(0.0, value))

    def _learning_multiplier(
        self, request: FeedbackRequest, credited_atom_ids: tuple[str, ...]
    ) -> float:
        if request.used_mem0:
            return 2.0
        for atom in self.repository.get_atoms(credited_atom_ids):
            if atom.metadata.get("source_system") == "mem0":
                return 2.0
        return 1.0

    @staticmethod
    def _with_evidence(existing: tuple[str, ...], feedback_id: str) -> tuple[str, ...]:
        return tuple(dict.fromkeys((*existing, feedback_id)))[-50:]

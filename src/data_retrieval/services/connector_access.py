from __future__ import annotations

import hashlib
import json

from data_retrieval.connectors.codec import outcome_to_mapping, query_to_mapping
from data_retrieval.connectors.contracts import (
    ContextPack,
    EvidenceResult,
    Outcome,
    Query,
    RecordModality,
)
from data_retrieval.connectors.projection import scope_namespace
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan, TemporalMode
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.repository import CortexRepository


class ConnectorAccessService:
    """Expose retrieval and attributable learning through connector identities."""

    def __init__(
        self,
        repository: CortexRepository,
        *,
        retrieval: RetrievalService | None = None,
    ) -> None:
        self.repository = repository
        self.retrieval = retrieval or RetrievalService(repository)

    def query(self, request: Query) -> ContextPack:
        fingerprint = _fingerprint(query_to_mapping(request))
        existing = self.repository.get_connector_query_receipt(request.request_id)
        if existing is not None:
            if existing[0] != fingerprint:
                raise ValueError("query request identity conflicts with different payload")
            return existing[1]

        result = self.retrieval.retrieve(
            QueryPlan(
                query=request.query,
                namespace=scope_namespace(request.scope),
                top_k=request.top_k,
                timeline_id=request.timeline_id,
                temporal_mode=TemporalMode(request.temporal_mode.value),
                as_of=request.as_of,
                range_start=request.range_start,
                range_end=request.range_end,
                reference_time=request.reference_time,
            )
        )
        items: list[EvidenceResult] = []
        used_tokens = 0
        for item in result.items:
            projection = self.repository.get_connector_projection_by_atom(item.atom_id)
            if projection is None:
                continue
            record = self.repository.get_connector_record(projection.record)
            if record is None:
                continue
            index = projection.atom_ids.index(item.atom_id)
            token_cost = _estimated_tokens(item.content)
            if (
                request.budget_tokens is not None
                and used_tokens + token_cost > request.budget_tokens
            ):
                continue
            lineage = tuple(
                evidence
                for atom_id in item.lineage_atom_ids
                if (evidence := self._evidence_for_atom(atom_id)) is not None
            )
            items.append(
                EvidenceResult(
                    evidence_id=projection.evidence_ids[index],
                    record=record.ref,
                    modality=RecordModality(record.modality.value),
                    content=item.content,
                    scope=record.scope,
                    score=item.score.final,
                    score_evidence=tuple(dict.fromkeys(item.score.evidence)),
                    lineage_evidence_ids=tuple(dict.fromkeys(lineage)),
                    metadata=record.metadata,
                )
            )
            used_tokens += token_cost
        context = ContextPack(
            retrieval_id=result.retrieval_id,
            query_request_id=request.request_id,
            items=tuple(items),
            low_confidence=result.low_confidence,
            budget_tokens=request.budget_tokens,
            used_tokens=used_tokens,
            abstention_reason=None if items else "no_relevant_evidence",
        )
        return self.repository.store_connector_query_receipt(
            fingerprint=fingerprint, context=context
        )

    def report_outcome(self, outcome: Outcome) -> Outcome:
        fingerprint = _fingerprint(outcome_to_mapping(outcome))
        existing = self.repository.get_connector_outcome_receipt(outcome.request_id)
        if existing is not None:
            if existing[0] != fingerprint:
                raise ValueError("outcome request identity conflicts with different payload")
            return existing[1]

        context = self.repository.get_connector_context(outcome.retrieval_id)
        if context is None:
            raise ValueError(f"unknown connector retrieval_id: {outcome.retrieval_id}")
        returned = {item.evidence_id for item in context.items}
        if not set(outcome.used_evidence_ids).issubset(returned):
            raise ValueError("outcome can only credit evidence returned by this retrieval")
        atom_ids = tuple(
            atom_id
            for evidence in outcome.used_evidence_ids
            if (atom_id := self.repository.get_connector_atom_id(evidence)) is not None
        )
        if len(atom_ids) != len(outcome.used_evidence_ids):
            raise ValueError("outcome contains unknown evidence")
        prior_feedback = self.repository.get_feedback_event(outcome.request_id)
        if prior_feedback is not None:
            expected = {
                "retrieval_id": outcome.retrieval_id,
                "outcome": outcome.outcome.value,
                "reason": outcome.reason,
                "occurred_at": outcome.occurred_at.isoformat(),
                "selected_atom_ids": sorted(atom_ids),
            }
            actual = {name: prior_feedback.get(name) for name in expected}
            if actual != expected:
                raise ValueError(
                    "outcome request identity conflicts with previously applied feedback"
                )
        else:
            LearningService(self.repository).apply_feedback(
                FeedbackRequest(
                    feedback_id=outcome.request_id,
                    retrieval_id=outcome.retrieval_id,
                    selected_atom_ids=atom_ids,
                    outcome=outcome.outcome.value,
                    reason=outcome.reason,
                    occurred_at=outcome.occurred_at,
                )
            )
        return self.repository.store_connector_outcome_receipt(
            fingerprint=fingerprint, outcome=outcome
        )

    def _evidence_for_atom(self, atom_id: str) -> str | None:
        projection = self.repository.get_connector_projection_by_atom(atom_id)
        if projection is None:
            return None
        return projection.evidence_ids[projection.atom_ids.index(atom_id)]


def _estimated_tokens(content: str) -> int:
    return max(1, (len(content) + 3) // 4)


def _fingerprint(value: dict[str, object]) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode()
    return hashlib.sha256(payload).hexdigest()

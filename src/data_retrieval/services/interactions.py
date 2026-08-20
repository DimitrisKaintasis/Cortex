from __future__ import annotations

from dataclasses import dataclass

from data_retrieval.domain.models import (
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    IngestionBundle,
)
from data_retrieval.retrieval.models import FeedbackRequest
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import FeedbackResult, LearningService
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class InteractionResult:
    conversation_id: str
    turn_id: str
    user_atom_ids: tuple[str, ...]
    assistant_atom_ids: tuple[str, ...]
    evidence_link_count: int
    feedback: FeedbackResult | None


class InteractionService:
    """Persist conversation turns and attribute explicit outcomes to retrieved evidence."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def record_turn(
        self,
        *,
        namespace: str,
        conversation_id: str,
        turn_id: str,
        user_text: str,
        assistant_text: str,
        retrieval_id: str | None = None,
        used_atom_ids: tuple[str, ...] = (),
        outcome: str | None = None,
        reason: str = "",
        used_mem0: bool = False,
        tags: tuple[str, ...] = (),
    ) -> InteractionResult:
        if not conversation_id.strip() or not turn_id.strip():
            raise ValueError("conversation_id and turn_id cannot be empty")
        if outcome is not None and retrieval_id is None:
            raise ValueError("an outcome requires retrieval_id")
        if outcome is not None and not used_atom_ids:
            raise ValueError("an outcome requires used_atom_ids")
        if retrieval_id is not None:
            retrieval = self.repository.get_retrieval_event(retrieval_id)
            if retrieval is None:
                raise ValueError(f"unknown retrieval_id: {retrieval_id}")
            returned_ids = {str(value) for value in retrieval.get("returned_atom_ids", [])}
            if not set(used_atom_ids).issubset(returned_ids):
                raise ValueError("used atoms must have been returned by the attributed retrieval")
        common_metadata = {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "transcript_reference": f"{conversation_id}:{turn_id}",
        }
        ingestion = IngestService(self.repository)
        user = ingestion.ingest_text(
            namespace=namespace,
            source=f"interaction:{conversation_id}:{turn_id}:user",
            text=user_text,
            explicit_tags=tags,
            metadata={**common_metadata, "interaction_role": "user"},
            atom_kind=AtomKind.INTERACTION,
        )
        assistant = ingestion.ingest_text(
            namespace=namespace,
            source=f"interaction:{conversation_id}:{turn_id}:assistant",
            text=assistant_text,
            explicit_tags=tags,
            metadata={
                **common_metadata,
                "interaction_role": "assistant",
                "retrieval_id": retrieval_id,
                "used_atom_ids": list(used_atom_ids),
            },
            atom_kind=AtomKind.INTERACTION,
        )
        links = tuple(
            AtomLink(
                from_atom_id=assistant_atom_id,
                to_atom_id=evidence_atom_id,
                relation=AtomLinkRelation.DERIVED_FROM,
                evidence_sources=(f"retrieval:{retrieval_id}",),
                metadata={"interaction_evidence": True},
            )
            for assistant_atom_id in assistant.atom_ids
            for evidence_atom_id in used_atom_ids
        )
        if links:
            document = self.repository.get_document(assistant.document_id)
            if document is None:
                raise ValueError("assistant interaction document disappeared")
            atoms = self.repository.get_atoms_for_document(assistant.document_id)
            self.repository.persist_ingestion(
                IngestionBundle(
                    document=document,
                    atoms=atoms,
                    tags=(),
                    atom_tags=(),
                    atom_links=links,
                )
            )

        feedback: FeedbackResult | None = None
        if outcome is not None and retrieval_id is not None:
            feedback = LearningService(self.repository).apply_feedback(
                FeedbackRequest(
                    feedback_id=f"interaction:{conversation_id}:{turn_id}",
                    retrieval_id=retrieval_id,
                    selected_atom_ids=used_atom_ids,
                    outcome=outcome,
                    reason=reason,
                    used_mem0=used_mem0,
                )
            )
        return InteractionResult(
            conversation_id=conversation_id,
            turn_id=turn_id,
            user_atom_ids=user.atom_ids,
            assistant_atom_ids=assistant.atom_ids,
            evidence_link_count=len(links),
            feedback=feedback,
        )

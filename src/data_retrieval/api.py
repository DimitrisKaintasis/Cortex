from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

from fastapi import FastAPI, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field

from data_retrieval.domain.models import TagCandidate, TagCandidateState
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan, TemporalMode
from data_retrieval.retrieval.ollama import OllamaEmbedder
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.tag_lifecycle import TagLifecycleService
from data_retrieval.storage.postgresql import PostgreSQLRepository
from data_retrieval.storage.repository import Repository
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.ollama import OllamaTagProposer

API_VERSION = "v1"
MAX_INGEST_CHARS = 2_000_000


@dataclass(frozen=True, slots=True)
class LocalApiConfig:
    """Process-local configuration; secrets are never included in API responses."""

    database_path: Path = Path("data.sqlite3")
    postgres_dsn: str | None = field(default=None, repr=False)
    ollama_url: str = "http://127.0.0.1:11435"
    ollama_timeout_seconds: float = 120.0
    tag_model: str | None = None
    embedding_model: str | None = None
    embedding_profile: str = "symmetric"

    @property
    def storage_kind(self) -> str:
        return "postgresql" if self.postgres_dsn else "sqlite"

    @contextmanager
    def open_repository(self) -> Iterator[Repository]:
        repository: Repository = (
            PostgreSQLRepository(self.postgres_dsn)
            if self.postgres_dsn
            else SQLiteRepository(self.database_path)
        )
        try:
            yield repository
        finally:
            repository.close()  # type: ignore[attr-defined]


class ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DocumentCreate(ApiModel):
    namespace: str = Field(min_length=1, max_length=200)
    source: str = Field(min_length=1, max_length=1_000)
    text: str = Field(min_length=1, max_length=MAX_INGEST_CHARS)
    tags: list[str] = Field(default_factory=list, max_length=200)
    occurred_at: datetime | None = None
    timeline_id: str | None = Field(default=None, max_length=500)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RetrievalCreate(ApiModel):
    namespace: str = Field(min_length=1, max_length=200)
    query: str = Field(min_length=1, max_length=20_000)
    tags: list[str] = Field(default_factory=list, max_length=200)
    top_k: int = Field(default=10, ge=1, le=100)
    timeline_id: str | None = Field(default=None, max_length=500)
    temporal_mode: TemporalMode = TemporalMode.AUTO
    as_of: datetime | None = None
    range_start: datetime | None = None
    range_end: datetime | None = None
    reference_time: datetime | None = None


class FeedbackCreate(ApiModel):
    feedback_id: str | None = Field(default=None, min_length=1, max_length=500)
    retrieval_id: str = Field(min_length=1, max_length=500)
    selected_atom_ids: list[str] = Field(min_length=1, max_length=100)
    outcome: Literal["positive", "negative"]
    reason: str = Field(default="", max_length=10_000)
    used_mem0: bool = False


class TagResolutionCreate(ApiModel):
    action: Literal["promote", "merge", "reject"]
    canonical_tag: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=10_000)


def create_app(config: LocalApiConfig | None = None) -> FastAPI:
    settings = config or LocalApiConfig()
    app = FastAPI(
        title="Data Retrieval Local API",
        version=API_VERSION,
        description=(
            "Local-only transport over canonical ingestion, explainable retrieval, "
            "tag review, and attributable feedback."
        ),
    )

    @app.exception_handler(ValueError)
    async def value_error_handler(_request: object, error: ValueError) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(error)})

    @app.get("/health")
    def health() -> dict[str, object]:
        with settings.open_repository() as repository:
            namespace_count = len(repository.list_namespaces())
        return {
            "status": "ok",
            "api_version": API_VERSION,
            "storage": settings.storage_kind,
            "namespace_count": namespace_count,
            "tag_model_configured": settings.tag_model is not None,
            "embedding_model_configured": settings.embedding_model is not None,
        }

    @app.get("/v1/namespaces")
    def list_namespaces(
        prefix: str | None = Query(default=None, max_length=200),
    ) -> dict[str, object]:
        with settings.open_repository() as repository:
            namespaces = repository.list_namespaces(prefix=prefix)
        return {"count": len(namespaces), "namespaces": namespaces}

    @app.post("/v1/documents", status_code=201)
    def create_document(request: DocumentCreate) -> dict[str, object]:
        metadata = dict(request.metadata)
        if request.timeline_id:
            metadata["timeline_id"] = request.timeline_id
        with settings.open_repository() as repository:
            result = IngestService(repository).ingest_text(
                namespace=request.namespace,
                source=request.source,
                text=request.text,
                explicit_tags=tuple(request.tags),
                occurred_at=request.occurred_at,
                metadata=metadata,
            )
        return {
            "document_id": result.document_id,
            "atom_ids": result.atom_ids,
            "tag_ids": result.tag_ids,
            "idempotent": result.idempotent,
        }

    @app.post("/v1/retrievals")
    def create_retrieval(request: RetrievalCreate) -> dict[str, object]:
        plan = QueryPlan(
            query=request.query,
            namespace=request.namespace,
            query_tags=tuple(request.tags),
            top_k=request.top_k,
            timeline_id=request.timeline_id,
            temporal_mode=request.temporal_mode,
            as_of=request.as_of,
            range_start=request.range_start,
            range_end=request.range_end,
            reference_time=request.reference_time,
        )
        with settings.open_repository() as repository:
            result = RetrievalService(
                repository,
                tag_proposer=_tag_proposer(settings),
                embedder=_embedder(settings),
            ).retrieve(plan)
        return {
            "retrieval_id": result.retrieval_id,
            "resolved_temporal_mode": result.resolved_temporal_mode.value,
            "low_confidence": result.low_confidence,
            "diagnostics": result.diagnostics,
            "items": [
                {
                    "atom_id": item.atom_id,
                    "content": item.content,
                    "kind": item.kind.value,
                    "occurred_at": (
                        item.occurred_at.isoformat() if item.occurred_at else None
                    ),
                    "role": item.role,
                    "atom_role": item.atom_role.value,
                    "temporal_label": item.temporal_label.value,
                    "metadata": item.metadata,
                    "lineage_atom_ids": item.lineage_atom_ids,
                    "score": {
                        "tag": item.score.tag,
                        "lexical": item.score.lexical,
                        "semantic": item.score.semantic,
                        "relationship": item.score.relationship,
                        "temporal": item.score.temporal,
                        "final": item.score.final,
                        "evidence": item.score.evidence,
                    },
                }
                for item in result.items
            ],
        }

    @app.post("/v1/feedback")
    def create_feedback(request: FeedbackCreate) -> dict[str, object]:
        with settings.open_repository() as repository:
            result = LearningService(repository).apply_feedback(
                FeedbackRequest(
                    feedback_id=request.feedback_id or str(uuid4()),
                    retrieval_id=request.retrieval_id,
                    selected_atom_ids=tuple(request.selected_atom_ids),
                    outcome=request.outcome,
                    reason=request.reason,
                    used_mem0=request.used_mem0,
                )
            )
        return {
            "feedback_id": result.feedback_id,
            "credited_atom_ids": result.credited_atom_ids,
            "atom_tag_updates": result.atom_tag_updates,
            "atom_link_updates": result.atom_link_updates,
            "tag_relation_updates": result.tag_relation_updates,
            "learning_multiplier": result.learning_multiplier,
        }

    @app.get("/v1/tag-candidates")
    def list_tag_candidates(
        namespace: str = Query(min_length=1, max_length=200),
        state: TagCandidateState = TagCandidateState.PROPOSED,
        limit: int = Query(default=100, ge=1, le=500),
    ) -> dict[str, object]:
        with settings.open_repository() as repository:
            candidates = repository.list_tag_candidates(
                namespace=namespace,
                state=state,
                limit=limit,
            )
            items = [_candidate_payload(repository, candidate) for candidate in candidates]
        return {"count": len(items), "candidates": items}

    @app.post("/v1/tag-candidates/{candidate_id}/resolution")
    def resolve_tag_candidate(
        candidate_id: str, request: TagResolutionCreate
    ) -> dict[str, object]:
        with settings.open_repository() as repository:
            service = TagLifecycleService(repository)
            if request.action == "promote":
                if request.canonical_tag or request.reason:
                    raise ValueError("promote does not accept canonical_tag or reason")
                result = service.promote(candidate_id)
            elif request.action == "merge":
                if not request.canonical_tag:
                    raise ValueError("merge requires canonical_tag")
                if request.reason:
                    raise ValueError("merge does not accept reason")
                result = service.merge(candidate_id, request.canonical_tag)
            else:
                if not request.reason:
                    raise ValueError("reject requires reason")
                if request.canonical_tag:
                    raise ValueError("reject does not accept canonical_tag")
                result = service.reject(candidate_id, reason=request.reason)
        return {
            "candidate_id": result.candidate.candidate_id,
            "state": result.candidate.state.value,
            "resolved_tag_id": result.candidate.resolved_tag_id,
            "canonical_tag": result.tag.canonical_text if result.tag else None,
            "atom_id": result.candidate.atom_id,
            "atom_tag_activated": result.atom_tag is not None,
            "resolution_reason": result.candidate.resolution_reason,
        }

    return app


def _tag_proposer(config: LocalApiConfig) -> OllamaTagProposer | None:
    if not config.tag_model:
        return None
    return OllamaTagProposer(
        base_url=config.ollama_url,
        model=config.tag_model,
        timeout_seconds=config.ollama_timeout_seconds,
    )


def _embedder(config: LocalApiConfig) -> OllamaEmbedder | None:
    if not config.embedding_model:
        return None
    return OllamaEmbedder(
        base_url=config.ollama_url,
        model_name=config.embedding_model,
        timeout_seconds=config.ollama_timeout_seconds,
        profile_name=config.embedding_profile,
    )


def _candidate_payload(
    repository: Repository, candidate: TagCandidate
) -> dict[str, object]:
    atom = repository.get_atom(candidate.atom_id)
    document = repository.get_document(atom.document_id) if atom else None
    return {
        "candidate_id": candidate.candidate_id,
        "atom_id": candidate.atom_id,
        "text": candidate.normalized_text,
        "display_text": candidate.display_text,
        "level": candidate.level.value,
        "confidence": candidate.confidence,
        "state": candidate.state.value,
        "producer": candidate.producer,
        "proposal_version": candidate.proposal_version,
        "resolved_tag_id": candidate.resolved_tag_id,
        "resolution_reason": candidate.resolution_reason,
        "created_at": candidate.created_at.isoformat(),
        "resolved_at": candidate.resolved_at.isoformat() if candidate.resolved_at else None,
        "evidence": (
            {
                "content": atom.content,
                "document_id": atom.document_id,
                "source": document.source if document else None,
                "position": atom.position,
            }
            if atom
            else None
        ),
    }

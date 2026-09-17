from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import utc_now
from data_retrieval.mem0 import (
    Mem0BootstrapService,
    Mem0Processor,
    Mem0VectorAdmissionPolicy,
    Mem0VectorCalibrationService,
)
from data_retrieval.retrieval.embedding import Embedder
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.large_ingestion import LargeFileIngestService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.repository import Repository, StagedIngestionRepository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer
from data_retrieval.tagging.proposals import TagProposer

if TYPE_CHECKING:
    from data_retrieval.temporal import TemporalBridge

PIPELINE_SCHEMA_VERSION = 1
CheckpointCallback = Callable[["DocumentPipelineReport"], None]


@dataclass(frozen=True, slots=True)
class TemporalPipelineRequest:
    timeline_id: str
    timezone_name: str
    range_start: datetime
    range_end: datetime
    state_path: Path
    max_workers: int = 1


@dataclass(frozen=True, slots=True)
class PipelineStageReport:
    name: str
    status: str
    started_at: datetime | None = None
    completed_at: datetime | None = None
    details: dict[str, Any] | None = None
    error_type: str | None = None
    error_message: str | None = None

    def as_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["started_at"] = self.started_at.isoformat() if self.started_at else None
        payload["completed_at"] = self.completed_at.isoformat() if self.completed_at else None
        return payload


@dataclass(frozen=True, slots=True)
class DocumentPipelineReport:
    schema_version: int
    run_id: str
    namespace: str
    source: str
    input_path: str
    input_hash: str
    ingestion_mode: str
    profile: dict[str, Any]
    status: str
    document_id: str | None
    created_at: datetime
    updated_at: datetime
    stages: tuple[PipelineStageReport, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "stages": [stage.as_dict() for stage in self.stages],
        }


class DocumentPipelineService:
    """Run the existing ingestion processors as one observable, retry-safe workflow."""

    def __init__(
        self,
        repository: Repository,
        *,
        tag_proposer: TagProposer | None = None,
        embedder: Embedder | None = None,
        temporal_bridge: TemporalBridge | None = None,
        mem0_processor: Mem0Processor | None = None,
        mem0_admission_policy: Mem0VectorAdmissionPolicy | None = None,
        ingest_batch_size: int = 1_000,
        mem0_atom_batch_size: int = 32,
        mem0_max_batch_chars: int = 24_000,
        mem0_accept_empty: bool = False,
    ) -> None:
        if ingest_batch_size <= 0:
            raise ValueError("ingest_batch_size must be positive")
        if mem0_atom_batch_size <= 0:
            raise ValueError("mem0_atom_batch_size must be positive")
        if mem0_max_batch_chars <= 0:
            raise ValueError("mem0_max_batch_chars must be positive")
        self.repository = repository
        self.tag_proposer = tag_proposer
        self.embedder = embedder
        self.temporal_bridge = temporal_bridge
        self.mem0_processor = mem0_processor
        self.mem0_admission_policy = mem0_admission_policy or Mem0VectorAdmissionPolicy()
        self.ingest_batch_size = ingest_batch_size
        self.mem0_atom_batch_size = mem0_atom_batch_size
        self.mem0_max_batch_chars = mem0_max_batch_chars
        self.mem0_accept_empty = mem0_accept_empty

    def run(
        self,
        *,
        path: Path,
        namespace: str,
        source: str,
        explicit_tags: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        temporal: TemporalPipelineRequest | None = None,
        mem0_user_id: str | None = None,
        checkpoint: CheckpointCallback | None = None,
    ) -> DocumentPipelineReport:
        if temporal is not None and self.temporal_bridge is None:
            raise ValueError("temporal request requires a Temporal bridge")
        input_hash = _file_hash(path)
        profile = self._profile(
            explicit_tags=explicit_tags,
            occurred_at=occurred_at,
            metadata=metadata,
            temporal=temporal,
        )
        now = utc_now()
        report = DocumentPipelineReport(
            schema_version=PIPELINE_SCHEMA_VERSION,
            run_id=stable_id(
                "document-pipeline-run",
                namespace,
                source,
                input_hash,
                content_hash(json.dumps(profile, sort_keys=True, default=str)),
            ),
            namespace=namespace,
            source=source,
            input_path=str(path.resolve()),
            input_hash=input_hash,
            ingestion_mode=(
                "bounded-staged"
                if isinstance(self.repository, StagedIngestionRepository)
                else "atomic-in-memory"
            ),
            profile=profile,
            status="running",
            document_id=None,
            created_at=now,
            updated_at=now,
            stages=(),
        )
        self._checkpoint(report, checkpoint)

        def stage(
            name: str,
            action: Callable[[], dict[str, Any]],
            *,
            enabled: bool = True,
        ) -> dict[str, Any] | None:
            nonlocal report
            if not enabled:
                report = self._append_stage(
                    report,
                    PipelineStageReport(name=name, status="skipped", completed_at=utc_now()),
                )
                self._checkpoint(report, checkpoint)
                return None
            started_at = utc_now()
            report = self._append_stage(
                report,
                PipelineStageReport(name=name, status="running", started_at=started_at),
            )
            self._checkpoint(report, checkpoint)
            try:
                details = action()
            except Exception as error:
                failed = replace(
                    report.stages[-1],
                    status="failed",
                    completed_at=utc_now(),
                    error_type=type(error).__name__,
                    error_message=str(error),
                )
                report = replace(
                    report,
                    status="failed",
                    updated_at=failed.completed_at or utc_now(),
                    stages=(*report.stages[:-1], failed),
                )
                self._checkpoint(report, checkpoint)
                raise
            completed = replace(
                report.stages[-1],
                status="completed",
                completed_at=utc_now(),
                details=details,
            )
            report = replace(
                report,
                updated_at=completed.completed_at or utc_now(),
                stages=(*report.stages[:-1], completed),
            )
            self._checkpoint(report, checkpoint)
            return details

        ingestion = stage(
            "canonical_ingestion",
            lambda: self._ingest(
                path=path,
                namespace=namespace,
                source=source,
                explicit_tags=explicit_tags,
                occurred_at=occurred_at,
                metadata=metadata,
            ),
        )
        assert ingestion is not None
        report = replace(report, document_id=str(ingestion["document_id"]), updated_at=utc_now())
        self._checkpoint(report, checkpoint)

        tag_service = (
            TagEnrichmentService(
                self.repository,
                self.tag_proposer,
                canonicalizer=(
                    SemanticTagCanonicalizer(self.embedder)
                    if self.embedder is not None
                    else None
                ),
            )
            if self.tag_proposer is not None
            else None
        )
        stage(
            "tag_enrichment",
            lambda: self._enrich_tags(tag_service, report.document_id),
            enabled=tag_service is not None,
        )
        stage(
            "temporal_projection",
            lambda: self._enrich_temporal(namespace, temporal, tag_service),
            enabled=temporal is not None,
        )
        stage(
            "mem0_bootstrap",
            lambda: self._bootstrap_mem0(namespace, mem0_user_id),
            enabled=self.mem0_processor is not None,
        )
        stage(
            "embedding_enrichment",
            lambda: self._enrich_embeddings(namespace),
            enabled=self.embedder is not None,
        )
        stage(
            "mem0_vector_calibration",
            lambda: self._calibrate_mem0(namespace),
            enabled=self.mem0_processor is not None and self.embedder is not None,
        )
        stage("weight_audit", lambda: self._audit(namespace))
        report = replace(report, status="completed", updated_at=utc_now())
        self._checkpoint(report, checkpoint)
        return report

    def _ingest(
        self,
        *,
        path: Path,
        namespace: str,
        source: str,
        explicit_tags: tuple[str, ...],
        occurred_at: datetime | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        if isinstance(self.repository, StagedIngestionRepository):
            result = LargeFileIngestService(
                self.repository,
                batch_size=self.ingest_batch_size,
            ).ingest_path(
                path=path,
                namespace=namespace,
                source=source,
                explicit_tags=explicit_tags,
                occurred_at=occurred_at,
                metadata=metadata,
            )
            return {**asdict(result), "bounded_ingestion": True}
        result = IngestService(self.repository).ingest_text(
            namespace=namespace,
            source=source,
            text=path.read_text(encoding="utf-8"),
            explicit_tags=explicit_tags,
            occurred_at=occurred_at,
            metadata=metadata,
        )
        return {
            **asdict(result),
            "atom_count": len(result.atom_ids),
            "bounded_ingestion": False,
        }

    @staticmethod
    def _enrich_tags(
        service: TagEnrichmentService | None, document_id: str | None
    ) -> dict[str, Any]:
        assert service is not None and document_id is not None
        return asdict(service.enrich_document(document_id))

    def _enrich_temporal(
        self,
        namespace: str,
        request: TemporalPipelineRequest | None,
        tag_service: TagEnrichmentService | None,
    ) -> dict[str, Any]:
        from data_retrieval.services.temporal_enrichment import TemporalEnrichmentService

        assert request is not None and self.temporal_bridge is not None
        result = TemporalEnrichmentService(
            self.repository,
            self.temporal_bridge,
        ).enrich_range(
            namespace=namespace,
            timeline_id=request.timeline_id,
            timezone_name=request.timezone_name,
            range_start=request.range_start,
            range_end=request.range_end,
            state_path=request.state_path,
            max_workers=request.max_workers,
        )
        tag_result = (
            tag_service.enrich_document(result.bundle.document.document_id)
            if tag_service is not None
            else None
        )
        return {
            "document_id": result.bundle.document.document_id,
            "summary_counts": result.summary_counts,
            "coverage_count": result.coverage_count,
            "generation": result.generation,
            "tag_enrichment": asdict(tag_result) if tag_result is not None else None,
            "state_path": str(request.state_path),
        }

    def _bootstrap_mem0(self, namespace: str, user_id: str | None) -> dict[str, Any]:
        assert self.mem0_processor is not None
        result = Mem0BootstrapService(
            self.repository,
            self.mem0_processor,
            atom_batch_size=self.mem0_atom_batch_size,
            max_batch_chars=self.mem0_max_batch_chars,
            accept_empty=self.mem0_accept_empty,
        ).run(namespace=namespace, user_id=user_id)
        return asdict(result)

    def _enrich_embeddings(self, namespace: str) -> dict[str, Any]:
        assert self.embedder is not None
        return asdict(
            EmbeddingEnrichmentService(self.repository, self.embedder).enrich_namespace(namespace)
        )

    def _calibrate_mem0(self, namespace: str) -> dict[str, Any]:
        assert self.embedder is not None
        return Mem0VectorCalibrationService(
            self.repository,
            self.embedder,
            policy=self.mem0_admission_policy,
        ).calibrate_namespace(namespace).as_dict()

    def _audit(self, namespace: str) -> dict[str, Any]:
        result = WeightLedgerService(self.repository).audit_namespace(namespace)
        if not result.passed:
            raise ValueError("weight ledger audit failed")
        return {
            "passed": result.passed,
            "target_count": result.target_count,
            "event_count": result.event_count,
            "mismatched_target_count": result.mismatched_target_count,
            "missing_event_target_count": result.missing_event_target_count,
        }

    def _profile(
        self,
        *,
        explicit_tags: tuple[str, ...],
        occurred_at: datetime | None,
        metadata: dict[str, Any] | None,
        temporal: TemporalPipelineRequest | None,
    ) -> dict[str, Any]:
        return {
            "ingestion": {
                "explicit_tags": explicit_tags,
                "occurred_at": occurred_at.isoformat() if occurred_at else None,
                "metadata_hash": content_hash(
                    json.dumps(metadata or {}, sort_keys=True, default=str)
                ),
                "batch_size": self.ingest_batch_size,
            },
            "tag_proposer": (
                {
                    "evidence_source": self.tag_proposer.evidence_source,
                    "proposal_version": self.tag_proposer.proposal_version,
                }
                if self.tag_proposer is not None
                else None
            ),
            "embedder": (
                {"provider": self.embedder.provider, "model": self.embedder.model}
                if self.embedder is not None
                else None
            ),
            "temporal": (
                {
                    "timeline_id": temporal.timeline_id,
                    "timezone_name": temporal.timezone_name,
                    "range_start": temporal.range_start.isoformat(),
                    "range_end": temporal.range_end.isoformat(),
                    "summarizer": getattr(
                        getattr(self.temporal_bridge, "summarizer", None),
                        "config_for_hash",
                        None,
                    ),
                }
                if temporal is not None
                else None
            ),
            "mem0": (
                str(getattr(self.mem0_processor, "profile_id", "configured"))
                if self.mem0_processor is not None
                else None
            ),
            "mem0_admission": asdict(self.mem0_admission_policy),
        }

    @staticmethod
    def _append_stage(
        report: DocumentPipelineReport, stage: PipelineStageReport
    ) -> DocumentPipelineReport:
        return replace(report, updated_at=utc_now(), stages=(*report.stages, stage))

    @staticmethod
    def _checkpoint(
        report: DocumentPipelineReport, checkpoint: CheckpointCallback | None
    ) -> None:
        if checkpoint is not None:
            checkpoint(report)


def _file_hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1_048_576):
            digest.update(block)
    return digest.hexdigest()

from __future__ import annotations

from dataclasses import dataclass

from data_retrieval.calibration import TeacherCalibrationService
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class CalibrationBackfillResult:
    namespace: str
    documents_examined: int
    documents_changed: int
    signals_created: int
    atom_tag_updates: int
    atom_link_updates: int
    tag_relation_updates: int
    truncated: bool


class CalibrationBackfillService:
    """Replay-safe calibration for data ingested before calibration was enabled."""

    def __init__(
        self,
        repository: Repository,
        *,
        document_batch_size: int = 250,
        atom_batch_size: int = 1_000,
    ) -> None:
        if document_batch_size <= 0:
            raise ValueError("document_batch_size must be positive")
        if atom_batch_size < 3:
            raise ValueError("atom_batch_size must be at least three")
        self.repository = repository
        self.document_batch_size = document_batch_size
        self.atom_batch_size = atom_batch_size
        self.calibrator = TeacherCalibrationService(repository)

    def run(
        self, *, namespace: str, max_documents: int | None = None
    ) -> CalibrationBackfillResult:
        if max_documents is not None and max_documents <= 0:
            raise ValueError("max_documents must be positive")
        examined = 0
        changed = 0
        signals = 0
        atom_tags = 0
        atom_links = 0
        tag_relations = 0
        truncated = False
        for document_ids in self.repository.iter_document_ids(
            namespace=namespace, batch_size=self.document_batch_size
        ):
            for document_id in document_ids:
                if max_documents is not None and examined >= max_documents:
                    truncated = True
                    break
                result = self.calibrator.calibrate_document_batched(
                    document_id, batch_size=self.atom_batch_size
                )
                examined += 1
                if not result.idempotent:
                    changed += 1
                    signals += result.signal_count
                    atom_tags += result.atom_tag_updates
                    atom_links += result.atom_link_updates
                    tag_relations += result.tag_relation_updates
            if truncated:
                break
        return CalibrationBackfillResult(
            namespace=namespace,
            documents_examined=examined,
            documents_changed=changed,
            signals_created=signals,
            atom_tag_updates=atom_tags,
            atom_link_updates=atom_links,
            tag_relation_updates=tag_relations,
            truncated=truncated,
        )

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data_retrieval.calibration import TeacherCalibrationService
from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomTag,
    Document,
    Tag,
    TagLevel,
    TagOrigin,
    TagState,
)
from data_retrieval.ingestion.chunker import TextChunker
from data_retrieval.storage.repository import Repository, StagedIngestionRepository
from data_retrieval.tagging.normalization import deduplicate_tags


@dataclass(frozen=True, slots=True)
class LargeIngestResult:
    document_id: str
    atom_count: int
    tag_ids: tuple[str, ...]
    idempotent: bool
    calibration_signals: int = 0


class LargeFileIngestService:
    """Two-pass, bounded-memory ingestion with restart-safe PostgreSQL batches."""

    def __init__(
        self,
        repository: Repository,
        *,
        chunker: TextChunker | None = None,
        batch_size: int = 1_000,
    ) -> None:
        if not isinstance(repository, StagedIngestionRepository):
            raise TypeError("repository does not support staged ingestion")
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.repository = repository
        self.chunker = chunker or TextChunker()
        self.batch_size = batch_size

    def ingest_path(
        self,
        *,
        path: Path,
        namespace: str,
        source: str,
        explicit_tags: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> LargeIngestResult:
        namespace = namespace.strip()
        source = source.strip()
        if not namespace:
            raise ValueError("namespace cannot be empty")
        if not source:
            raise ValueError("source cannot be empty")
        if occurred_at is not None and occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")

        document_hash, has_content = self._hash_text(path)
        if not has_content:
            raise ValueError("text cannot be empty")
        document_id = stable_id("doc", namespace, source, document_hash)
        existing = self.repository.get_document(document_id)
        if existing is not None:
            calibration = TeacherCalibrationService(
                self.repository
            ).calibrate_document_batched(document_id, batch_size=max(3, self.batch_size))
            return LargeIngestResult(
                document_id=document_id,
                atom_count=self.repository.get_document_atom_count(document_id),
                tag_ids=(),
                idempotent=True,
                calibration_signals=calibration.signal_count,
            )

        source_metadata = dict(metadata or {})
        document = Document(
            document_id=document_id,
            namespace=namespace,
            source=source,
            content_hash=document_hash,
            metadata=source_metadata,
        )
        requested = deduplicate_tags(explicit_tags)
        existing_tags = {
            tag.canonical_text: tag
            for tag in self.repository.get_tags_by_canonical(
                namespace=namespace,
                canonical_texts=tuple(canonical for canonical, _ in requested),
            )
        }
        tags: list[Tag] = []
        origins: dict[str, TagOrigin] = {}
        for canonical, display in requested:
            tag = existing_tags.get(canonical)
            if tag is None:
                tag = Tag(
                    tag_id=stable_id("tag", namespace, canonical),
                    namespace=namespace,
                    canonical_text=canonical,
                    display_text=display,
                    level=TagLevel.SPECIFIC if " " in canonical else TagLevel.BROAD,
                    state=TagState.PROPOSED_NEW,
                )
                origins[tag.tag_id] = TagOrigin.PROPOSED_NEW
            else:
                origins[tag.tag_id] = TagOrigin.CATALOG_MATCH
            tags.append(tag)

        if not self.repository.begin_staged_ingestion(document=document, tags=tuple(tags)):
            calibration = TeacherCalibrationService(
                self.repository
            ).calibrate_document_batched(document_id, batch_size=max(3, self.batch_size))
            return LargeIngestResult(
                document_id=document_id,
                atom_count=self.repository.get_document_atom_count(document_id),
                tag_ids=(),
                idempotent=True,
                calibration_signals=calibration.signal_count,
            )

        atom_batch: list[Atom] = []
        edge_batch: list[AtomTag] = []
        atom_count = 0
        with path.open("r", encoding="utf-8", newline=None) as stream:
            for chunk in self.chunker.iter_stream(stream):
                atom = Atom(
                    atom_id=stable_id(
                        "atom", document_id, str(chunk.position), content_hash(chunk.text)
                    ),
                    document_id=document_id,
                    namespace=namespace,
                    position=chunk.position,
                    char_start=chunk.char_start,
                    char_end=chunk.char_end,
                    content=chunk.text,
                    content_hash=content_hash(chunk.text),
                    occurred_at=occurred_at,
                    metadata={**source_metadata, "source": source},
                )
                atom_batch.append(atom)
                edge_batch.extend(
                    AtomTag(
                        atom_id=atom.atom_id,
                        tag_id=tag.tag_id,
                        weight_raw=1.0,
                        confidence=1.0,
                        origin=origins[tag.tag_id],
                        evidence_sources=("explicit",),
                    )
                    for tag in tags
                )
                atom_count += 1
                if len(atom_batch) >= self.batch_size:
                    self._flush(atom_batch, edge_batch)
            self._flush(atom_batch, edge_batch)

        self.repository.complete_staged_ingestion(
            document_id=document_id, atom_count=atom_count
        )
        calibration = TeacherCalibrationService(
            self.repository
        ).calibrate_document_batched(document_id, batch_size=max(3, self.batch_size))
        return LargeIngestResult(
            document_id=document_id,
            atom_count=atom_count,
            tag_ids=tuple(tag.tag_id for tag in tags),
            idempotent=False,
            calibration_signals=calibration.signal_count,
        )

    def _flush(self, atoms: list[Atom], edges: list[AtomTag]) -> None:
        if not atoms:
            return
        self.repository.append_staged_ingestion(
            atoms=tuple(atoms), atom_tags=tuple(edges)
        )
        atoms.clear()
        edges.clear()

    @staticmethod
    def _hash_text(path: Path) -> tuple[str, bool]:
        digest = hashlib.sha256()
        has_content = False
        with path.open("r", encoding="utf-8", newline=None) as stream:
            while block := stream.read(1_048_576):
                digest.update(block.encode("utf-8"))
                has_content = has_content or bool(block.strip())
        return digest.hexdigest(), has_content

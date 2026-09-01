from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from data_retrieval.calibration.teachers import TeacherCalibrationService
from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomKind,
    AtomRole,
    AtomTag,
    Document,
    IngestionBundle,
    PayloadModality,
    Tag,
    TagLevel,
    TagOrigin,
    TagState,
)
from data_retrieval.ingestion.chunker import TextChunker
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.normalization import deduplicate_tags


@dataclass(frozen=True, slots=True)
class IngestResult:
    document_id: str
    atom_ids: tuple[str, ...]
    tag_ids: tuple[str, ...]
    idempotent: bool


class IngestService:
    def __init__(
        self,
        repository: Repository,
        chunker: TextChunker | None = None,
    ) -> None:
        self.repository = repository
        self.chunker = chunker or TextChunker()

    def ingest_text(
        self,
        *,
        namespace: str,
        source: str,
        text: str,
        explicit_tags: tuple[str, ...] = (),
        occurred_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
        atom_kind: AtomKind = AtomKind.SOURCE,
        atom_role: AtomRole | None = None,
        payload_modality: PayloadModality = PayloadModality.TEXT,
    ) -> IngestResult:
        namespace = namespace.strip()
        source = source.strip()
        if not namespace:
            raise ValueError("namespace cannot be empty")
        if not source:
            raise ValueError("source cannot be empty")
        if occurred_at is not None and occurred_at.tzinfo is None:
            raise ValueError("occurred_at must include a timezone")

        document_hash = content_hash(text)
        document_id = stable_id("doc", namespace, source, document_hash)
        existing_document = self.repository.get_document(document_id)
        if existing_document:
            atoms = self.repository.get_atoms_for_document(document_id)
            TeacherCalibrationService(self.repository).calibrate_document(document_id)
            return IngestResult(
                document_id=document_id,
                atom_ids=tuple(atom.atom_id for atom in atoms),
                tag_ids=(),
                idempotent=True,
            )

        chunks = self.chunker.split(text)
        source_metadata = dict(metadata or {})
        document = Document(
            document_id=document_id,
            namespace=namespace,
            source=source,
            content_hash=document_hash,
            metadata=source_metadata,
        )

        atoms = tuple(
            Atom(
                atom_id=stable_id(
                    "atom",
                    document_id,
                    str(chunk.position),
                    content_hash(chunk.text),
                ),
                document_id=document_id,
                namespace=namespace,
                position=chunk.position,
                char_start=chunk.char_start,
                char_end=chunk.char_end,
                content=chunk.text,
                content_hash=content_hash(chunk.text),
                kind=atom_kind,
                role=atom_role,
                modality=payload_modality,
                occurred_at=occurred_at,
                metadata={**source_metadata, "source": source},
            )
            for chunk in chunks
        )

        normalized_tags = deduplicate_tags(explicit_tags)
        existing_catalog = {
            tag.canonical_text: tag
            for tag in self.repository.get_tags_by_canonical(
                namespace=namespace,
                canonical_texts=tuple(canonical for canonical, _ in normalized_tags),
            )
        }
        tags_by_canonical: dict[str, Tag] = {}
        origins: dict[str, TagOrigin] = {}
        edge_evidence: dict[tuple[str, str], set[str]] = {}
        edge_confidence: dict[tuple[str, str], float] = {}

        def resolve_tag(canonical: str, display: str) -> Tag:
            resolved = tags_by_canonical.get(canonical)
            if resolved:
                return resolved
            resolved = existing_catalog.get(canonical)
            if resolved:
                origins[resolved.tag_id] = TagOrigin.CATALOG_MATCH
            else:
                resolved = Tag(
                    tag_id=stable_id("tag", namespace, canonical),
                    namespace=namespace,
                    canonical_text=canonical,
                    display_text=display,
                    level=self._infer_level(canonical),
                    state=TagState.PROPOSED_NEW,
                )
                origins[resolved.tag_id] = TagOrigin.PROPOSED_NEW
            tags_by_canonical[canonical] = resolved
            return resolved

        def attach_tag(*, atom_id: str, tag: Tag, confidence: float, evidence_source: str) -> None:
            key = (atom_id, tag.tag_id)
            edge_evidence.setdefault(key, set()).add(evidence_source)
            edge_confidence[key] = max(confidence, edge_confidence.get(key, 0.0))

        for canonical, display in normalized_tags:
            tag = resolve_tag(canonical, display)
            for atom in atoms:
                attach_tag(
                    atom_id=atom.atom_id,
                    tag=tag,
                    confidence=1.0,
                    evidence_source="explicit",
                )

        atom_tags = tuple(
            AtomTag(
                atom_id=atom_id,
                tag_id=tag_id,
                weight_raw=1.0,
                confidence=edge_confidence[(atom_id, tag_id)],
                origin=origins[tag_id],
                evidence_sources=tuple(sorted(evidence)),
            )
            for (atom_id, tag_id), evidence in sorted(edge_evidence.items())
        )
        tags = tuple(sorted(tags_by_canonical.values(), key=lambda tag: tag.canonical_text))

        self.repository.persist_ingestion(
            IngestionBundle(
                document=document,
                atoms=atoms,
                tags=tags,
                atom_tags=atom_tags,
            )
        )
        TeacherCalibrationService(self.repository).calibrate_document(document_id)
        return IngestResult(
            document_id=document_id,
            atom_ids=tuple(atom.atom_id for atom in atoms),
            tag_ids=tuple(tag.tag_id for tag in tags),
            idempotent=False,
        )

    @staticmethod
    def _infer_level(canonical_tag: str) -> TagLevel:
        return TagLevel.SPECIFIC if " " in canonical_tag else TagLevel.BROAD

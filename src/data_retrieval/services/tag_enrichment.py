from __future__ import annotations

from dataclasses import dataclass, replace

from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import (
    AtomTag,
    IngestionBundle,
    Tag,
    TagLevel,
    TagOrigin,
    TagState,
    utc_now,
)
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.normalization import normalize_tag
from data_retrieval.tagging.proposals import TagProposer

MARKER_ROOT = "data_retrieval_enrichments"


@dataclass(frozen=True, slots=True)
class TagEnrichmentResult:
    document_id: str
    tag_ids: tuple[str, ...]
    atom_tag_count: int
    idempotent: bool


class TagEnrichmentService:
    """Apply optional tag proposals after canonical ingestion has succeeded."""

    def __init__(self, repository: Repository, proposer: TagProposer) -> None:
        self.repository = repository
        self.proposer = proposer

    def enrich_document(self, document_id: str) -> TagEnrichmentResult:
        document = self.repository.get_document(document_id)
        if document is None:
            raise ValueError(f"unknown document_id: {document_id}")
        atoms = self.repository.get_atoms_for_document(document_id)
        marker_key = f"{self.proposer.evidence_source}:{self.proposer.proposal_version}"
        markers = dict(document.metadata.get(MARKER_ROOT, {}))
        tag_markers = dict(markers.get("tag_proposals", {}))
        if marker_key in tag_markers:
            return TagEnrichmentResult(
                document_id=document_id,
                tag_ids=tuple(tag_markers[marker_key].get("tag_ids", ())),
                atom_tag_count=sum(
                    len(self.repository.atom_tags_for(atom.atom_id)) for atom in atoms
                ),
                idempotent=True,
            )

        catalog = {tag.canonical_text: tag for tag in self.repository.list_tags(document.namespace)}
        tags_by_canonical: dict[str, Tag] = {}
        all_edges: dict[tuple[str, str], AtomTag] = {
            (edge.atom_id, edge.tag_id): edge
            for atom in atoms
            for edge in self.repository.atom_tags_for(atom.atom_id)
        }

        for atom in atoms:
            existing_tags = tuple(sorted(set(catalog) | set(tags_by_canonical)))
            proposals = self.proposer.propose_tags(
                text=atom.content,
                namespace=document.namespace,
                existing_tags=existing_tags,
            )
            best: dict[str, tuple[str, float]] = {}
            for proposal in proposals:
                canonical = normalize_tag(proposal.text)
                current = best.get(canonical)
                if canonical and (current is None or proposal.confidence > current[1]):
                    best[canonical] = (proposal.text.strip(), proposal.confidence)

            for canonical, (display, confidence) in best.items():
                tag = tags_by_canonical.get(canonical) or catalog.get(canonical)
                if tag is None:
                    tag = Tag(
                        tag_id=stable_id("tag", document.namespace, canonical),
                        namespace=document.namespace,
                        canonical_text=canonical,
                        display_text=display,
                        level=(TagLevel.SPECIFIC if " " in canonical else TagLevel.BROAD),
                        state=TagState.PROPOSED_NEW,
                    )
                tags_by_canonical[canonical] = tag
                key = (atom.atom_id, tag.tag_id)
                existing = all_edges.get(key)
                evidence = set(existing.evidence_sources if existing else ())
                evidence.add(self.proposer.evidence_source)
                all_edges[key] = AtomTag(
                    atom_id=atom.atom_id,
                    tag_id=tag.tag_id,
                    weight_raw=existing.weight_raw if existing else 1.0,
                    confidence=max(confidence, existing.confidence if existing else 0.0),
                    origin=(
                        existing.origin
                        if existing
                        else (
                            TagOrigin.CATALOG_MATCH
                            if canonical in catalog
                            else TagOrigin.PROPOSED_NEW
                        )
                    ),
                    evidence_sources=tuple(sorted(evidence)),
                    created_at=existing.created_at if existing else utc_now(),
                )

        tag_ids = tuple(
            tag.tag_id
            for tag in sorted(tags_by_canonical.values(), key=lambda item: item.canonical_text)
        )
        tag_markers[marker_key] = {
            "evidence_source": self.proposer.evidence_source,
            "proposal_version": self.proposer.proposal_version,
            "tag_ids": list(tag_ids),
        }
        markers["tag_proposals"] = tag_markers
        enriched_document = replace(
            document,
            metadata={**document.metadata, MARKER_ROOT: markers},
        )
        self.repository.persist_ingestion(
            IngestionBundle(
                document=enriched_document,
                atoms=atoms,
                tags=tuple(tags_by_canonical.values()),
                atom_tags=tuple(all_edges.values()),
            )
        )
        return TagEnrichmentResult(
            document_id=document_id,
            tag_ids=tag_ids,
            atom_tag_count=len(all_edges),
            idempotent=False,
        )

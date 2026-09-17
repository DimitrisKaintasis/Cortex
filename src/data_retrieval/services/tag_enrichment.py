from __future__ import annotations

from dataclasses import dataclass, replace

from data_retrieval.calibration.tag_similarity import TagSimilarityCalibrationService
from data_retrieval.calibration.teachers import TeacherCalibrationService
from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import (
    AtomTag,
    IngestionBundle,
    Tag,
    TagCandidate,
    TagCandidateState,
    TagOrigin,
    utc_now,
)
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer
from data_retrieval.tagging.normalization import normalize_tag
from data_retrieval.tagging.proposals import BatchTagProposer, TagProposal, TagProposer

MARKER_ROOT = "data_retrieval_enrichments"


@dataclass(frozen=True, slots=True)
class TagEnrichmentResult:
    document_id: str
    tag_ids: tuple[str, ...]
    candidate_ids: tuple[str, ...]
    atom_tag_count: int
    proposed_count: int
    resolved_count: int
    idempotent: bool


class TagEnrichmentService:
    """Apply optional tag proposals after canonical ingestion has succeeded."""

    def __init__(
        self,
        repository: Repository,
        proposer: TagProposer,
        *,
        catalog_hint_limit: int = 500,
        canonicalizer: SemanticTagCanonicalizer | None = None,
        similarity_calibration: TagSimilarityCalibrationService | None = None,
    ) -> None:
        if catalog_hint_limit <= 0:
            raise ValueError("catalog_hint_limit must be positive")
        if (
            similarity_calibration is not None
            and similarity_calibration.repository is not repository
        ):
            raise ValueError("similarity calibration must use the enrichment repository")
        self.repository = repository
        self.proposer = proposer
        self.catalog_hint_limit = catalog_hint_limit
        self.canonicalizer = canonicalizer
        self.similarity_calibration = similarity_calibration

    def enrich_document(self, document_id: str) -> TagEnrichmentResult:
        document = self.repository.get_document(document_id)
        if document is None:
            raise ValueError(f"unknown document_id: {document_id}")
        atoms = self.repository.get_atoms_for_document(document_id)
        marker_key = f"{self.proposer.evidence_source}:{self.proposer.proposal_version}"
        markers = dict(document.metadata.get(MARKER_ROOT, {}))
        tag_markers = dict(markers.get("tag_proposals", {}))
        if marker_key in tag_markers:
            TeacherCalibrationService(self.repository).calibrate_document(document_id)
            if self.similarity_calibration is not None:
                self.similarity_calibration.calibrate(document.namespace)
            return TagEnrichmentResult(
                document_id=document_id,
                tag_ids=tuple(tag_markers[marker_key].get("tag_ids", ())),
                candidate_ids=tuple(tag_markers[marker_key].get("candidate_ids", ())),
                atom_tag_count=sum(
                    len(self.repository.atom_tags_for(atom.atom_id)) for atom in atoms
                ),
                proposed_count=int(tag_markers[marker_key].get("proposed_count", 0)),
                resolved_count=int(tag_markers[marker_key].get("resolved_count", 0)),
                idempotent=True,
            )

        catalog = {
            tag.canonical_text: tag
            for tag in self.repository.list_tags(document.namespace, limit=self.catalog_hint_limit)
        }
        tags_by_canonical: dict[str, Tag] = {}
        candidates_by_id: dict[str, TagCandidate] = {}
        all_edges: dict[tuple[str, str], AtomTag] = {
            (edge.atom_id, edge.tag_id): edge
            for atom in atoms
            for edge in self.repository.atom_tags_for(atom.atom_id)
        }

        existing_tags = tuple(sorted(catalog))
        if isinstance(self.proposer, BatchTagProposer):
            proposed_by_atom = self.proposer.propose_tags_batch(
                texts=tuple(atom.content for atom in atoms),
                namespace=document.namespace,
                existing_tags=existing_tags,
            )
        else:
            proposed_by_atom = tuple(
                self.proposer.propose_tags(
                    text=atom.content,
                    namespace=document.namespace,
                    existing_tags=existing_tags,
                )
                for atom in atoms
            )

        semantic_matches = (
            self.canonicalizer.resolve(
                candidates=tuple(
                    proposal.text
                    for proposals in proposed_by_atom
                    for proposal in proposals
                    if normalize_tag(proposal.text) not in catalog
                ),
                catalog=tuple(catalog.values()),
            )
            if self.canonicalizer is not None
            else {}
        )

        for atom, proposals in zip(atoms, proposed_by_atom, strict=True):
            best: dict[str, TagProposal] = {}
            for proposal in proposals:
                canonical = normalize_tag(proposal.text)
                current = best.get(canonical)
                if canonical and (current is None or proposal.confidence > current.confidence):
                    best[canonical] = proposal

            missing = tuple(
                canonical
                for canonical in best
                if canonical not in catalog and canonical not in tags_by_canonical
            )
            catalog.update(
                (tag.canonical_text, tag)
                for tag in self.repository.get_tags_by_canonical(
                    namespace=document.namespace, canonical_texts=missing
                )
            )

            for canonical, proposal in best.items():
                confidence = proposal.confidence
                semantic_match = semantic_matches.get(canonical)
                semantic_alias: str | None = None
                resolution_reason: str | None = None
                candidate_state = TagCandidateState.PROPOSED
                tag = catalog.get(canonical)
                if tag is not None:
                    candidate_state = TagCandidateState.CANONICALIZED
                    resolution_reason = "exact catalog match"
                if semantic_match is not None:
                    semantic_alias = canonical
                    canonical = semantic_match.tag.canonical_text
                    confidence = min(confidence, semantic_match.similarity)
                    tag = semantic_match.tag
                    candidate_state = TagCandidateState.MERGED
                    resolution_reason = f"semantic catalog match:{semantic_match.similarity:.6f}"
                tag = tags_by_canonical.get(canonical) or tag
                if tag is not None and semantic_alias and semantic_alias not in tag.aliases:
                    tag = replace(tag, aliases=tuple(sorted((*tag.aliases, semantic_alias))))
                now = utc_now()
                candidate = TagCandidate(
                    candidate_id=stable_id(
                        "tag-candidate",
                        atom.atom_id,
                        normalize_tag(proposal.text),
                        self.proposer.evidence_source,
                        self.proposer.proposal_version,
                    ),
                    namespace=document.namespace,
                    atom_id=atom.atom_id,
                    normalized_text=normalize_tag(proposal.text),
                    display_text=proposal.text.strip(),
                    level=proposal.level,
                    confidence=proposal.confidence,
                    state=candidate_state,
                    producer=self.proposer.evidence_source,
                    proposal_version=self.proposer.proposal_version,
                    resolved_tag_id=tag.tag_id if tag is not None else None,
                    resolution_reason=resolution_reason,
                    created_at=now,
                    resolved_at=(now if tag is not None else None),
                )
                candidates_by_id[candidate.candidate_id] = candidate
                if tag is None:
                    continue
                tags_by_canonical[canonical] = tag
                key = (atom.atom_id, tag.tag_id)
                existing = all_edges.get(key)
                evidence = set(existing.evidence_sources if existing else ())
                evidence.add(self.proposer.evidence_source)
                if semantic_alias and self.canonicalizer is not None:
                    evidence.add(self.canonicalizer.evidence_source)
                all_edges[key] = AtomTag(
                    atom_id=atom.atom_id,
                    tag_id=tag.tag_id,
                    weight_raw=existing.weight_raw if existing else 1.0,
                    confidence=max(confidence, existing.confidence if existing else 0.0),
                    origin=(existing.origin if existing else TagOrigin.CATALOG_MATCH),
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
            "candidate_ids": sorted(candidates_by_id),
            "proposed_count": sum(
                candidate.state is TagCandidateState.PROPOSED
                for candidate in candidates_by_id.values()
            ),
            "resolved_count": sum(
                candidate.state is not TagCandidateState.PROPOSED
                for candidate in candidates_by_id.values()
            ),
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
                tag_candidates=tuple(candidates_by_id.values()),
            )
        )
        TeacherCalibrationService(self.repository).calibrate_document(document_id)
        if self.similarity_calibration is not None:
            self.similarity_calibration.calibrate(document.namespace)
        return TagEnrichmentResult(
            document_id=document_id,
            tag_ids=tag_ids,
            candidate_ids=tuple(sorted(candidates_by_id)),
            atom_tag_count=len(all_edges),
            proposed_count=sum(
                candidate.state is TagCandidateState.PROPOSED
                for candidate in candidates_by_id.values()
            ),
            resolved_count=sum(
                candidate.state is not TagCandidateState.PROPOSED
                for candidate in candidates_by_id.values()
            ),
            idempotent=False,
        )

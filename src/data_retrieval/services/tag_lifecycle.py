from __future__ import annotations

from dataclasses import dataclass, replace

from data_retrieval.calibration.teachers import TeacherCalibrationService
from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import (
    AtomTag,
    Tag,
    TagCandidate,
    TagCandidateState,
    TagOrigin,
    TagState,
    utc_now,
)
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.normalization import normalize_tag


@dataclass(frozen=True, slots=True)
class TagResolutionResult:
    candidate: TagCandidate
    tag: Tag | None
    atom_tag: AtomTag | None


class TagLifecycleService:
    """Review quarantined tag candidates and atomically activate accepted concepts."""

    def __init__(self, repository: Repository) -> None:
        self.repository = repository

    def promote(self, candidate_id: str) -> TagResolutionResult:
        candidate = self._proposed(candidate_id)
        existing = self.repository.get_tags_by_canonical(
            namespace=candidate.namespace,
            canonical_texts=(candidate.normalized_text,),
        )
        if existing:
            raise ValueError(
                "a canonical tag with this text already exists; merge the candidate instead"
            )
        tag = Tag(
            tag_id=stable_id("tag", candidate.namespace, candidate.normalized_text),
            namespace=candidate.namespace,
            canonical_text=candidate.normalized_text,
            display_text=candidate.display_text,
            level=candidate.level,
            state=TagState.CANONICAL,
        )
        return self._activate(
            candidate,
            tag,
            state=TagCandidateState.CANONICALIZED,
            reason="reviewed and promoted to canonical tag",
        )

    def merge(self, candidate_id: str, canonical_text: str) -> TagResolutionResult:
        candidate = self._proposed(candidate_id)
        normalized = normalize_tag(canonical_text)
        if not normalized:
            raise ValueError("canonical_text cannot be empty")
        matches = self.repository.get_tags_by_canonical(
            namespace=candidate.namespace,
            canonical_texts=(normalized,),
        )
        if not matches:
            raise ValueError(f"unknown canonical tag: {normalized}")
        tag = matches[0]
        if (
            candidate.normalized_text != tag.canonical_text
            and candidate.normalized_text not in tag.aliases
        ):
            tag = replace(
                tag,
                aliases=tuple(sorted((*tag.aliases, candidate.normalized_text))),
            )
        return self._activate(
            candidate,
            tag,
            state=TagCandidateState.MERGED,
            reason=f"reviewed and merged into:{tag.canonical_text}",
        )

    def reject(self, candidate_id: str, *, reason: str) -> TagResolutionResult:
        candidate = self._proposed(candidate_id)
        cleaned_reason = reason.strip()
        if not cleaned_reason:
            raise ValueError("rejection reason cannot be empty")
        resolved = replace(
            candidate,
            state=TagCandidateState.REJECTED,
            resolution_reason=cleaned_reason,
            resolved_at=utc_now(),
        )
        self.repository.apply_tag_candidate_resolution(
            candidate=resolved,
            tag=None,
            atom_tag=None,
        )
        return TagResolutionResult(candidate=resolved, tag=None, atom_tag=None)

    def _activate(
        self,
        candidate: TagCandidate,
        tag: Tag,
        *,
        state: TagCandidateState,
        reason: str,
    ) -> TagResolutionResult:
        existing = next(
            (
                edge
                for edge in self.repository.atom_tags_for(candidate.atom_id)
                if edge.tag_id == tag.tag_id
            ),
            None,
        )
        evidence = set(existing.evidence_sources if existing else ())
        evidence.update(
            {
                candidate.producer,
                f"proposal-version:{candidate.proposal_version}",
                "tag-review",
            }
        )
        now = utc_now()
        atom_tag = AtomTag(
            atom_id=candidate.atom_id,
            tag_id=tag.tag_id,
            weight_raw=existing.weight_raw if existing else 1.0,
            confidence=max(candidate.confidence, existing.confidence if existing else 0.0),
            origin=existing.origin if existing else TagOrigin.PROMOTED_PROPOSAL,
            evidence_sources=tuple(sorted(evidence)),
            created_at=existing.created_at if existing else now,
            updated_at=now,
        )
        resolved = replace(
            candidate,
            state=state,
            resolved_tag_id=tag.tag_id,
            resolution_reason=reason,
            resolved_at=now,
        )
        self.repository.apply_tag_candidate_resolution(
            candidate=resolved,
            tag=tag,
            atom_tag=atom_tag,
        )
        atom = self.repository.get_atom(candidate.atom_id)
        if atom is None:
            raise ValueError(f"unknown atom_id: {candidate.atom_id}")
        TeacherCalibrationService(self.repository).calibrate_document(atom.document_id)
        calibrated_edge = next(
            edge
            for edge in self.repository.atom_tags_for(candidate.atom_id)
            if edge.tag_id == tag.tag_id
        )
        return TagResolutionResult(candidate=resolved, tag=tag, atom_tag=calibrated_edge)

    def _proposed(self, candidate_id: str) -> TagCandidate:
        candidate = self.repository.get_tag_candidate(candidate_id)
        if candidate is None:
            raise ValueError(f"unknown candidate_id: {candidate_id}")
        if candidate.state is not TagCandidateState.PROPOSED:
            raise ValueError(f"candidate is already resolved: {candidate_id}")
        return candidate

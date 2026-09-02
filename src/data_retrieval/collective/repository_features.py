from __future__ import annotations

import math
import statistics
import time
from collections import Counter
from dataclasses import asdict, dataclass
from typing import Any

from data_retrieval.domain.models import (
    Atom,
    AtomLink,
    AtomLinkRelation,
    CalibrationTarget,
    WeightEventSource,
)
from data_retrieval.retrieval.embedding import cosine_similarity
from data_retrieval.storage.repository import Repository

from .features import (
    CollectiveFeatureTriage,
    ContributorSupport,
    FeatureExtractionRequest,
    FeatureTriageResult,
    ImpactFeatureEvidence,
    LedgerFeatureEvidence,
    Mem0FeatureEvidence,
    PolicyFeatureEvidence,
    TemporalFeatureEvidence,
    VectorFeatureEvidence,
)
from .models import SharedConcept


@dataclass(frozen=True, slots=True)
class RepositoryFeatureCandidate:
    """A payload-free description of one repository relationship to inspect."""

    entry_id: str
    namespace: str
    source_tag_id: str
    target_tag_id: str
    relation_type: str
    source_atom_ids: tuple[str, ...]
    target_atom_ids: tuple[str, ...]
    embedding_provider: str | None = None
    embedding_model: str | None = None
    approved_alignment_confidence: float | None = None
    impact: ImpactFeatureEvidence | None = None
    policy: PolicyFeatureEvidence = PolicyFeatureEvidence()

    def __post_init__(self) -> None:
        for name in (
            "entry_id",
            "namespace",
            "source_tag_id",
            "target_tag_id",
            "relation_type",
        ):
            if not getattr(self, name).strip():
                raise ValueError(f"{name} cannot be empty")
        if self.source_tag_id == self.target_tag_id:
            raise ValueError("repository feature candidate requires two different tags")
        if (self.embedding_provider is None) != (self.embedding_model is None):
            raise ValueError("embedding provider and model must be supplied together")


@dataclass(frozen=True, slots=True)
class RepositoryFeatureAdapterPolicy:
    profile: str = "repository-feature-adapter-v1"
    maximum_atoms_per_side: int = 64
    duplicate_similarity: float = 0.98
    shadow_weight_increment: float = 0.05

    def __post_init__(self) -> None:
        if not self.profile.strip():
            raise ValueError("adapter profile cannot be empty")
        if self.maximum_atoms_per_side <= 0:
            raise ValueError("maximum_atoms_per_side must be positive")
        if not 0.0 <= self.duplicate_similarity <= 1.0:
            raise ValueError("duplicate_similarity must be in [0, 1]")
        if not math.isfinite(self.shadow_weight_increment) or self.shadow_weight_increment <= 0:
            raise ValueError("shadow_weight_increment must be finite and positive")


@dataclass(frozen=True, slots=True)
class RepositoryFeatureObservation:
    entry_id: str
    source_concept_id: str
    target_concept_id: str
    disposition: str
    priority: float
    reason_codes: tuple[str, ...]
    evidence_sources: tuple[str, ...]
    missing_sources: tuple[str, ...]
    triage_features: dict[str, Any]
    feature_components: tuple[dict[str, Any], ...]
    duration_ms: float

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class RepositoryFeatureObservationReport:
    profile: str
    namespace: str
    stored_relation_count: int
    eligible_relation_count: int
    skipped_missing_catalog_tags: int
    truncated_candidate_count: int
    candidate_count: int
    duration_ms: float
    mean_candidate_ms: float
    p95_candidate_ms: float
    disposition_counts: dict[str, int]
    reason_counts: dict[str, int]
    missing_source_counts: dict[str, int]
    feature_distributions: dict[str, dict[str, float]]
    observations: tuple[RepositoryFeatureObservation, ...]
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "observations": [item.as_dict() for item in self.observations],
        }


class ShadowRepositoryEvidenceAdapter:
    """Read repository evidence for cheap triage without writes or model calls."""

    def __init__(
        self,
        repository: Repository,
        *,
        policy: RepositoryFeatureAdapterPolicy | None = None,
        triage: CollectiveFeatureTriage | None = None,
    ) -> None:
        self.repository = repository
        self.policy = policy or RepositoryFeatureAdapterPolicy()
        self.triage = triage or CollectiveFeatureTriage()

    def candidates_for_namespace(
        self,
        *,
        namespace: str,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        limit: int | None = None,
    ) -> tuple[RepositoryFeatureCandidate, ...]:
        if (embedding_provider is None) != (embedding_model is None):
            raise ValueError("embedding provider and model must be supplied together")
        tags = {tag.tag_id: tag for tag in self.repository.list_tags(namespace)}
        atom_tags = self.repository.list_atom_tags(namespace)
        atoms_by_tag: dict[str, list[str]] = {}
        for edge in atom_tags:
            atoms_by_tag.setdefault(edge.tag_id, []).append(edge.atom_id)
        relations = self.repository.list_tag_relations(namespace=namespace)
        candidates: list[RepositoryFeatureCandidate] = []
        for relation in relations:
            source = tags.get(relation.source_tag_id)
            target = tags.get(relation.target_tag_id)
            if source is None or target is None:
                continue
            candidates.append(
                RepositoryFeatureCandidate(
                    entry_id=(
                        f"tag-relation:{relation.source_tag_id}:"
                        f"{relation.target_tag_id}:{relation.relation_type}"
                    ),
                    namespace=namespace,
                    source_tag_id=relation.source_tag_id,
                    target_tag_id=relation.target_tag_id,
                    relation_type=relation.relation_type,
                    source_atom_ids=self._bounded_ids(
                        atoms_by_tag.get(relation.source_tag_id, [])
                    ),
                    target_atom_ids=self._bounded_ids(
                        atoms_by_tag.get(relation.target_tag_id, [])
                    ),
                    embedding_provider=embedding_provider,
                    embedding_model=embedding_model,
                )
            )
            if limit is not None and len(candidates) >= limit:
                break
        return tuple(candidates)

    def extract(self, candidate: RepositoryFeatureCandidate) -> FeatureExtractionRequest:
        tags = {
            tag.tag_id: tag
            for tag in self.repository.get_tags(
                (candidate.source_tag_id, candidate.target_tag_id)
            )
        }
        if candidate.source_tag_id not in tags or candidate.target_tag_id not in tags:
            raise ValueError("candidate tags must exist in the repository")
        source = tags[candidate.source_tag_id]
        target = tags[candidate.target_tag_id]
        if source.namespace != candidate.namespace or target.namespace != candidate.namespace:
            raise ValueError("candidate tags must belong to the requested namespace")
        source_ids = self._bounded_ids(candidate.source_atom_ids)
        target_ids = self._bounded_ids(candidate.target_atom_ids)
        atom_ids = tuple(sorted(set((*source_ids, *target_ids))))
        atoms = {atom.atom_id: atom for atom in self.repository.get_atoms(atom_ids)}
        source_atoms = tuple(
            atoms[atom_id]
            for atom_id in source_ids
            if atom_id in atoms
        )
        target_atoms = tuple(
            atoms[atom_id]
            for atom_id in target_ids
            if atom_id in atoms
        )
        evidence_atoms = tuple(
            {
                atom.atom_id: atom
                for atom in (*source_atoms, *target_atoms)
            }.values()
        )
        links = self._relevant_links(evidence_atoms)
        return FeatureExtractionRequest(
            entry_id=candidate.entry_id,
            source_concept_id=SharedConcept.from_key(source.canonical_text).concept_id,
            target_concept_id=SharedConcept.from_key(target.canonical_text).concept_id,
            ledger=self._ledger(candidate),
            approved_alignment_confidence=candidate.approved_alignment_confidence,
            vectors=self._vectors(candidate, source_atoms, target_atoms),
            mem0=self._mem0(evidence_atoms, links),
            temporal=self._temporal(evidence_atoms, links),
            impact=candidate.impact or self._impact(candidate),
            policy=candidate.policy,
        )

    def evaluate(self, candidate: RepositoryFeatureCandidate) -> FeatureTriageResult:
        return self.triage.evaluate(self.extract(candidate))

    def observe_namespace(
        self,
        *,
        namespace: str,
        embedding_provider: str | None = None,
        embedding_model: str | None = None,
        limit: int | None = None,
    ) -> RepositoryFeatureObservationReport:
        started = time.perf_counter()
        catalog_tag_ids = {
            tag.tag_id for tag in self.repository.list_tags(namespace)
        }
        stored_relations = self.repository.list_tag_relations(namespace=namespace)
        eligible_relation_count = sum(
            relation.source_tag_id in catalog_tag_ids
            and relation.target_tag_id in catalog_tag_ids
            for relation in stored_relations
        )
        candidates = self.candidates_for_namespace(
            namespace=namespace,
            embedding_provider=embedding_provider,
            embedding_model=embedding_model,
            limit=limit,
        )
        observations: list[RepositoryFeatureObservation] = []
        for candidate in candidates:
            candidate_started = time.perf_counter()
            result = self.evaluate(candidate)
            elapsed = (time.perf_counter() - candidate_started) * 1_000
            observations.append(
                RepositoryFeatureObservation(
                    entry_id=result.decision.entry_id,
                    source_concept_id=result.features.triage.source_concept_id,
                    target_concept_id=result.features.triage.target_concept_id,
                    disposition=result.decision.disposition.value,
                    priority=result.decision.priority,
                    reason_codes=result.decision.reason_codes,
                    evidence_sources=result.features.evidence_sources,
                    missing_sources=result.features.missing_sources,
                    triage_features=asdict(result.features.triage),
                    feature_components=tuple(
                        asdict(component) for component in result.features.components
                    ),
                    duration_ms=elapsed,
                )
            )
        duration = (time.perf_counter() - started) * 1_000
        latencies = [item.duration_ms for item in observations]
        return RepositoryFeatureObservationReport(
            profile=self.policy.profile,
            namespace=namespace,
            stored_relation_count=len(stored_relations),
            eligible_relation_count=eligible_relation_count,
            skipped_missing_catalog_tags=(
                len(stored_relations) - eligible_relation_count
            ),
            truncated_candidate_count=max(
                0, eligible_relation_count - len(observations)
            ),
            candidate_count=len(observations),
            duration_ms=duration,
            mean_candidate_ms=statistics.fmean(latencies) if latencies else 0.0,
            p95_candidate_ms=self._percentile(latencies, 0.95),
            disposition_counts=dict(Counter(item.disposition for item in observations)),
            reason_counts=dict(
                Counter(reason for item in observations for reason in item.reason_codes)
            ),
            missing_source_counts=dict(
                Counter(source for item in observations for source in item.missing_sources)
            ),
            feature_distributions=self._feature_distributions(observations),
            observations=tuple(observations),
            limitations=(
                "Current local weight events do not prove independent contributors; "
                "the adapter therefore assigns zero independent-contributor maturity.",
                "Only already-cached embeddings are read; missing vectors remain missing.",
                "Impact is a local outgoing-weight perturbation, not a full retrieval replay.",
                "Concept IDs are experimental mappings from namespace-local canonical tags.",
            ),
        )

    def _ledger(self, candidate: RepositoryFeatureCandidate) -> LedgerFeatureEvidence:
        relations = self.repository.get_tag_relations_touching(
            tag_ids=(candidate.source_tag_id, candidate.target_tag_id),
            relation_type=candidate.relation_type,
        )
        relationship_exists = any(
            relation.source_tag_id == candidate.source_tag_id
            and relation.target_tag_id == candidate.target_tag_id
            for relation in relations
        )
        events = self.repository.list_weight_events(
            namespace=candidate.namespace,
            target_type=CalibrationTarget.TAG_RELATION,
            target_id=candidate.source_tag_id,
            related_id=candidate.target_tag_id,
            relation_type=candidate.relation_type,
        )
        feedback = tuple(
            event for event in events if event.source_type is WeightEventSource.FEEDBACK
        )
        positive = sum(max(0.0, event.delta) for event in feedback)
        negative = sum(max(0.0, -event.delta) for event in feedback)
        contributors = (
            (
                ContributorSupport(
                    contributor_bucket="unattributed-local-feedback",
                    positive_support=positive,
                    negative_support=negative,
                ),
            )
            if positive + negative > 0
            else ()
        )
        return LedgerFeatureEvidence(
            relationship_exists=relationship_exists,
            contributors=contributors,
            concept_independent_contributors=0,
            contributor_independence_known=False,
            profile=f"{self.policy.profile}:local-weight-events",
        )

    def _vectors(
        self,
        candidate: RepositoryFeatureCandidate,
        source_atoms: tuple[Atom, ...],
        target_atoms: tuple[Atom, ...],
    ) -> VectorFeatureEvidence | None:
        if candidate.embedding_provider is None or candidate.embedding_model is None:
            return None
        all_atoms = tuple({atom.atom_id: atom for atom in (*source_atoms, *target_atoms)}.values())
        embeddings = self.repository.get_embeddings(
            atom_ids=tuple(atom.atom_id for atom in all_atoms),
            provider=candidate.embedding_provider,
            model=candidate.embedding_model,
        )
        valid = {
            atom.atom_id: embeddings[atom.atom_id]
            for atom in all_atoms
            if atom.atom_id in embeddings
            and embeddings[atom.atom_id].content_hash == atom.content_hash
        }
        left = [valid[atom.atom_id] for atom in source_atoms if atom.atom_id in valid]
        right = [valid[atom.atom_id] for atom in target_atoms if atom.atom_id in valid]
        if not left or not right:
            return None
        similarities = sorted(
            (
                self._bounded_similarity(cosine_similarity(a.vector, b.vector))
                for a in left
                for b in right
                if a.atom_id != b.atom_id
            ),
            reverse=True,
        )
        if not similarities:
            return None
        combined = list(valid.values())
        duplicates = 0
        representatives = []
        for embedding in combined:
            if any(
                self._bounded_similarity(
                    cosine_similarity(embedding.vector, existing.vector)
                )
                >= self.policy.duplicate_similarity
                for existing in representatives
            ):
                duplicates += 1
            else:
                representatives.append(embedding)
        return VectorFeatureEvidence(
            best_similarity=similarities[0],
            second_similarity=similarities[1] if len(similarities) > 1 else 0.0,
            duplicate_fraction=duplicates / len(combined),
            provider=candidate.embedding_provider,
            model=candidate.embedding_model,
            calibration_profile=(
                f"{self.policy.profile}:cached-cosine-margin-v1:"
                f"{candidate.embedding_provider}:{candidate.embedding_model}"
            ),
        )

    def _impact(
        self, candidate: RepositoryFeatureCandidate
    ) -> ImpactFeatureEvidence:
        outgoing = tuple(
            relation
            for relation in self.repository.get_tag_relations_touching(
                tag_ids=(candidate.source_tag_id,)
            )
            if relation.source_tag_id == candidate.source_tag_id
        )
        current_weights = {
            (relation.target_tag_id, relation.relation_type): relation.weight_raw
            for relation in outgoing
        }
        selected = (candidate.target_tag_id, candidate.relation_type)
        candidate_weights = dict(current_weights)
        candidate_weights[selected] = (
            candidate_weights.get(selected, 0.0) + self.policy.shadow_weight_increment
        )
        current_influences = self._relative_shares(current_weights)
        candidate_influences = self._relative_shares(candidate_weights)
        current_rank = self._rank(current_influences, selected)
        candidate_rank = self._rank(candidate_influences, selected)
        unrelated = set(current_influences) | set(candidate_influences)
        unrelated.discard(selected)
        unrelated_max_delta = max(
            (
                abs(
                    candidate_influences.get(key, 0.0)
                    - current_influences.get(key, 0.0)
                )
                for key in unrelated
            ),
            default=0.0,
        )
        return ImpactFeatureEvidence(
            current_influence=current_influences.get(selected, 0.0),
            candidate_influence=candidate_influences.get(selected, 0.0),
            unrelated_max_delta=unrelated_max_delta,
            target_rank_change=current_rank - candidate_rank,
            profile=f"{self.policy.profile}:local-relative-weight-perturbation-v1",
        )

    def _relevant_links(self, evidence_atoms: tuple[Atom, ...]) -> tuple[AtomLink, ...]:
        if not evidence_atoms:
            return ()
        seed_ids = tuple(atom.atom_id for atom in evidence_atoms)
        first = self.repository.get_atom_links_touching(atom_ids=seed_ids)
        endpoint_ids = tuple(
            sorted(
                {
                    endpoint
                    for link in first
                    for endpoint in (link.from_atom_id, link.to_atom_id)
                }
            )
        )
        second = (
            self.repository.get_atom_links_touching(atom_ids=endpoint_ids)
            if endpoint_ids
            else ()
        )
        return tuple(
            {
                (link.from_atom_id, link.to_atom_id, link.relation): link
                for link in (*first, *second)
            }.values()
        )

    def _mem0(
        self,
        evidence_atoms: tuple[Atom, ...],
        links: tuple[AtomLink, ...],
    ) -> Mem0FeatureEvidence | None:
        seed_ids = {atom.atom_id for atom in evidence_atoms}
        all_ids = {
            endpoint
            for link in links
            for endpoint in (link.from_atom_id, link.to_atom_id)
        }
        atoms = {
            atom.atom_id: atom
            for atom in self.repository.get_atoms(tuple(sorted(seed_ids | all_ids)))
        }
        mem0_ids = {
            atom_id
            for atom_id, atom in atoms.items()
            if atom.metadata.get("source_system") == "mem0"
        }
        relevant_mem0 = set(seed_ids & mem0_ids)
        for link in links:
            if link.relation is not AtomLinkRelation.SUPPORTED_BY:
                continue
            if link.from_atom_id in mem0_ids and link.to_atom_id in seed_ids:
                relevant_mem0.add(link.from_atom_id)
            if link.to_atom_id in mem0_ids and link.from_atom_id in seed_ids:
                relevant_mem0.add(link.to_atom_id)
        if not relevant_mem0:
            return None
        support_map: dict[str, set[str]] = {atom_id: set() for atom_id in mem0_ids}
        for link in links:
            if link.relation is AtomLinkRelation.SUPPORTED_BY:
                if link.from_atom_id in mem0_ids:
                    support_map[link.from_atom_id].add(link.to_atom_id)
                elif link.to_atom_id in mem0_ids:
                    support_map[link.to_atom_id].add(link.from_atom_id)
        supporting = {
            lineage
            for atom_id in relevant_mem0
            for lineage in (support_map.get(atom_id) or {atom_id})
        }
        conflicting: set[str] = set()
        conflict_found = False
        for link in links:
            if link.relation is not AtomLinkRelation.CONFLICTS_WITH:
                continue
            if link.from_atom_id in relevant_mem0:
                conflict_found = True
                other = link.to_atom_id
            elif link.to_atom_id in relevant_mem0:
                conflict_found = True
                other = link.from_atom_id
            else:
                continue
            conflicting.update(support_map.get(other) or {other})
        return Mem0FeatureEvidence(
            supporting_lineages=tuple(sorted(supporting)),
            conflicting_lineages=tuple(sorted(conflicting)),
            update_detected=conflict_found,
            profile=f"{self.policy.profile}:mem0-native-links-v1",
        )

    def _temporal(
        self,
        evidence_atoms: tuple[Atom, ...],
        links: tuple[AtomLink, ...],
    ) -> TemporalFeatureEvidence | None:
        seed_ids = {atom.atom_id for atom in evidence_atoms}
        endpoint_ids = {
            endpoint
            for link in links
            for endpoint in (link.from_atom_id, link.to_atom_id)
        }
        endpoint_atoms = self.repository.get_atoms(tuple(sorted(endpoint_ids)))
        temporal_atom_ids = {
            atom.atom_id
            for atom in endpoint_atoms
            if atom.metadata.get("engine") == "temporal-history"
        }
        temporal_links = tuple(
            link
            for link in links
            if link.relation is AtomLinkRelation.SUPERSEDES
            or (
                link.relation
                in {AtomLinkRelation.SUMMARIZES, AtomLinkRelation.DERIVED_FROM}
                and (
                    link.from_atom_id in temporal_atom_ids
                    or link.to_atom_id in temporal_atom_ids
                )
            )
        )
        has_time = any(atom.occurred_at is not None for atom in evidence_atoms)
        if not has_time and not temporal_links:
            return None
        superseded = {
            link.to_atom_id
            for link in links
            if link.relation is AtomLinkRelation.SUPERSEDES
            and link.to_atom_id in seed_ids
        }
        conflict_ids = {
            endpoint
            for link in links
            if link.relation is AtomLinkRelation.CONFLICTS_WITH
            and not link.metadata.get("source_system") == "mem0"
            for endpoint in (link.from_atom_id, link.to_atom_id)
            if endpoint in seed_ids
        }
        denominator = max(1, len(seed_ids))
        superseded_ratio = len(superseded) / denominator
        return TemporalFeatureEvidence(
            current_support_ratio=(len(seed_ids - superseded) / denominator),
            superseded_ratio=superseded_ratio,
            temporal_conflict_ratio=len(conflict_ids) / denominator,
            profile=f"{self.policy.profile}:temporal-native-links-v1",
        )

    def _bounded_ids(self, values: Any) -> tuple[str, ...]:
        return tuple(sorted(set(values)))[: self.policy.maximum_atoms_per_side]

    @staticmethod
    def _relative_shares(weights: dict[tuple[str, str], float]) -> dict[tuple[str, str], float]:
        total = sum(max(0.0, weight) for weight in weights.values())
        if total <= 0.0:
            return {}
        return {key: max(0.0, weight) / total for key, weight in weights.items()}

    @staticmethod
    def _rank(
        influences: dict[tuple[str, str], float], selected: tuple[str, str]
    ) -> int:
        ordered = sorted(
            influences,
            key=lambda key: (influences[key], key),
            reverse=True,
        )
        return ordered.index(selected) + 1 if selected in ordered else len(ordered) + 1

    @staticmethod
    def _bounded_similarity(value: float) -> float:
        if not math.isfinite(value):
            return 0.0
        return min(1.0, max(0.0, value))

    @staticmethod
    def _percentile(values: list[float], percentile: float) -> float:
        if not values:
            return 0.0
        ordered = sorted(values)
        index = math.ceil(percentile * len(ordered)) - 1
        return ordered[max(0, min(index, len(ordered) - 1))]

    @staticmethod
    def _feature_distributions(
        observations: list[RepositoryFeatureObservation],
    ) -> dict[str, dict[str, float]]:
        if not observations:
            return {}
        names = (
            "uncertainty",
            "expected_impact",
            "risk",
            "conflict",
            "contributor_concentration",
            "concept_rarity",
            "alignment_confidence",
        )
        result: dict[str, dict[str, float]] = {}
        for name in names:
            values = sorted(float(item.triage_features[name]) for item in observations)
            result[name] = {
                "minimum": values[0],
                "median": statistics.median(values),
                "p95": ShadowRepositoryEvidenceAdapter._percentile(values, 0.95),
                "maximum": values[-1],
            }
        return result

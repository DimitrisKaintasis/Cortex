from __future__ import annotations

import math
import re
from collections import defaultdict
from uuid import uuid4

from data_retrieval.domain.models import Atom, AtomKind, AtomLinkRelation, utc_now
from data_retrieval.retrieval.embedding import Embedder, cosine_similarity
from data_retrieval.retrieval.models import (
    QueryPlan,
    RetrievalItem,
    RetrievalResult,
    ScoreBreakdown,
    SearchHit,
    TemporalMode,
)
from data_retrieval.retrieval.planning import QueryPlanner
from data_retrieval.retrieval.temporal_lens import TemporalLens
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer
from data_retrieval.tagging.normalization import normalize_tag
from data_retrieval.tagging.proposals import TagProposer

TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", re.UNICODE)


class RetrievalService:
    """Explainable hybrid retrieval with conditional temporal semantics."""

    def __init__(
        self,
        repository: Repository,
        *,
        tag_proposer: TagProposer | None = None,
        embedder: Embedder | None = None,
        planner: QueryPlanner | None = None,
        temporal_lens: TemporalLens | None = None,
        low_confidence_threshold: float = 0.20,
        candidate_limit: int = 500,
        catalog_hint_limit: int = 500,
        tag_canonicalizer: SemanticTagCanonicalizer | None = None,
    ) -> None:
        if candidate_limit <= 0:
            raise ValueError("candidate_limit must be positive")
        if catalog_hint_limit <= 0:
            raise ValueError("catalog_hint_limit must be positive")
        self.repository = repository
        self.tag_proposer = tag_proposer
        self.embedder = embedder
        self.planner = planner or QueryPlanner()
        self.temporal_lens = temporal_lens or TemporalLens()
        self.low_confidence_threshold = low_confidence_threshold
        self.candidate_limit = candidate_limit
        self.catalog_hint_limit = catalog_hint_limit
        self.tag_canonicalizer = tag_canonicalizer or (
            SemanticTagCanonicalizer(embedder) if embedder is not None else None
        )

    def retrieve(self, requested_plan: QueryPlan) -> RetrievalResult:
        auto_temporal = requested_plan.temporal_mode is TemporalMode.AUTO
        plan = self.planner.resolve(requested_plan)
        query_tags, tag_warnings = self._query_tags(plan)

        tag_hits = self.repository.search_tag_hits(
            namespace=plan.namespace,
            canonical_tags=query_tags,
            limit=self.candidate_limit,
        )
        lexical_hits = self.repository.search_lexical_hits(
            namespace=plan.namespace,
            query=plan.query,
            limit=self.candidate_limit,
        )
        semantic_hits, semantic_warning = self._bounded_semantic_hits(plan)
        raw_tag_scores, tag_evidence = self._hit_maps(tag_hits)
        raw_lexical_scores, lexical_evidence = self._hit_maps(lexical_hits)
        raw_semantic_scores, semantic_evidence = self._hit_maps(semantic_hits)
        warnings = [*tag_warnings]
        if semantic_warning:
            warnings.append(semantic_warning)

        tag_scores = self._normalize(raw_tag_scores)
        lexical_scores = self._normalize(raw_lexical_scores)
        semantic_scores = self._normalize(raw_semantic_scores)
        base_candidate_ids = set(tag_scores) | set(lexical_scores) | set(semantic_scores)
        raw_relationship_scores, relationship_evidence = self._bounded_relationship_scores(
            namespace=plan.namespace,
            query_tags=query_tags,
            seed_atom_ids=base_candidate_ids,
        )
        relationship_scores = self._normalize(raw_relationship_scores)
        candidate_ids = base_candidate_ids | set(relationship_scores)

        links = self.repository.get_atom_links_touching(atom_ids=tuple(candidate_ids))
        for link in links:
            if (
                link.relation is AtomLinkRelation.SUPERSEDES
                and link.to_atom_id in candidate_ids
            ):
                candidate_ids.add(link.from_atom_id)
                raw_relationship_scores.setdefault(
                    link.from_atom_id, link.weight_raw * link.confidence
                )
                relationship_evidence.setdefault(
                    link.from_atom_id, (f"supersedes={link.to_atom_id}",)
                )
        relationship_scores = self._normalize(raw_relationship_scores)
        context_ids = set(candidate_ids)
        for link in links:
            context_ids.update((link.from_atom_id, link.to_atom_id))
        atoms = self.repository.get_atoms(tuple(sorted(context_ids)))
        atom_lookup = {atom.atom_id: atom for atom in atoms}
        candidate_ids.intersection_update(atom_lookup)
        assessment = self.temporal_lens.assess(
            plan=plan,
            atoms=atoms,
            links=links,
            auto_detect_state=auto_temporal,
        )
        candidate_ids.intersection_update(assessment.eligible_atom_ids)

        channel_weights = self._channel_weights(
            has_tags=bool(query_tags), has_semantic=bool(semantic_scores)
        )
        lineage = self._summary_lineage(links)
        ranked: list[RetrievalItem] = []
        for atom_id in candidate_ids:
            atom = atom_lookup[atom_id]
            tag_score = tag_scores.get(atom_id, 0.0)
            lexical_score = lexical_scores.get(atom_id, 0.0)
            semantic_score = semantic_scores.get(atom_id, 0.0)
            relationship_score = relationship_scores.get(atom_id, 0.0)
            temporal_score = assessment.temporal_scores.get(atom_id, 0.0)
            final = min(
                1.0,
                channel_weights["tag"] * tag_score
                + channel_weights["lexical"] * lexical_score
                + channel_weights["semantic"] * semantic_score
                + 0.12 * relationship_score
                + temporal_score,
            )
            evidence = tuple(
                [
                    *tag_evidence.get(atom_id, ()),
                    *lexical_evidence.get(atom_id, ()),
                    *semantic_evidence.get(atom_id, ()),
                    *relationship_evidence.get(atom_id, ()),
                    *((f"temporal={temporal_score:.3f}",) if temporal_score else ()),
                ]
            )
            ranked.append(
                RetrievalItem(
                    atom_id=atom_id,
                    content=atom.content,
                    kind=atom.kind,
                    occurred_at=atom.occurred_at,
                    role=assessment.roles.get(
                        atom_id,
                        "continuity"
                        if atom.kind is AtomKind.TEMPORAL_SUMMARY
                        else "source_evidence",
                    ),
                    score=ScoreBreakdown(
                        tag=tag_score,
                        lexical=lexical_score,
                        semantic=semantic_score,
                        relationship=relationship_score,
                        temporal=temporal_score,
                        final=final,
                        evidence=evidence,
                    ),
                    metadata=atom.metadata,
                    lineage_atom_ids=lineage.get(atom_id, ()),
                )
            )

        ranked.sort(key=self._sort_key, reverse=True)
        selected = self._pack_without_duplicate_summaries(ranked, plan.top_k)
        top_score = selected[0].score.final if selected else 0.0
        retrieval_id = str(uuid4())
        result = RetrievalResult(
            retrieval_id=retrieval_id,
            plan=plan,
            resolved_temporal_mode=assessment.resolved_mode,
            items=tuple(selected),
            low_confidence=top_score < self.low_confidence_threshold,
            diagnostics={
                "query_tags": query_tags,
                "channel_weights": channel_weights,
                "candidate_counts": {
                    "tag": len(tag_scores),
                    "lexical": len(lexical_scores),
                    "semantic": len(semantic_scores),
                    "relationship": len(relationship_scores),
                    "eligible_union": len(candidate_ids),
                },
                "superseded_atom_ids": sorted(assessment.superseded_atom_ids),
                "warnings": warnings,
            },
        )
        self.repository.record_retrieval_event(
            {
                "retrieval_id": retrieval_id,
                "namespace": plan.namespace,
                "query": plan.query,
                "query_tags": list(query_tags),
                "temporal_mode": assessment.resolved_mode.value,
                "returned_atom_ids": [item.atom_id for item in selected],
                "scores": {
                    item.atom_id: {
                        "tag": item.score.tag,
                        "lexical": item.score.lexical,
                        "semantic": item.score.semantic,
                        "relationship": item.score.relationship,
                        "temporal": item.score.temporal,
                        "final": item.score.final,
                    }
                    for item in selected
                },
                "created_at": utc_now().isoformat(),
            }
        )
        return result

    @staticmethod
    def _hit_maps(
        hits: tuple[SearchHit, ...],
    ) -> tuple[dict[str, float], dict[str, tuple[str, ...]]]:
        return (
            {hit.atom_id: hit.score for hit in hits},
            {hit.atom_id: hit.evidence for hit in hits},
        )

    def _bounded_semantic_hits(
        self, plan: QueryPlan
    ) -> tuple[tuple[SearchHit, ...], str | None]:
        if self.embedder is None:
            return (), None
        try:
            query_vector = self.embedder.embed_query(plan.query)
            if not query_vector:
                return (), "semantic_query_embedding_invalid"
            return (
                self.repository.search_semantic_hits(
                    namespace=plan.namespace,
                    provider=self.embedder.provider,
                    model=self.embedder.model,
                    query_vector=query_vector,
                    limit=self.candidate_limit,
                ),
                None,
            )
        except Exception as error:  # noqa: BLE001 - retrieval must degrade safely
            return (), f"semantic_unavailable:{type(error).__name__}"

    def _bounded_relationship_scores(
        self,
        *,
        namespace: str,
        query_tags: tuple[str, ...],
        seed_atom_ids: set[str],
    ) -> tuple[dict[str, float], dict[str, tuple[str, ...]]]:
        scores: dict[str, float] = defaultdict(float)
        evidence: dict[str, list[str]] = defaultdict(list)
        query_tag_records = self.repository.get_tags_by_canonical(
            namespace=namespace, canonical_texts=query_tags
        )
        query_tag_ids = {tag.tag_id for tag in query_tag_records}
        relations = self.repository.get_tag_relations_touching(tag_ids=tuple(query_tag_ids))
        related_strength_by_id: dict[str, float] = defaultdict(float)
        related_evidence_by_id: dict[str, str] = {}
        for relation in relations:
            strength = relation.weight_raw * relation.confidence
            if relation.relation_type == "co_occurs":
                if relation.source_tag_id in query_tag_ids:
                    related_strength_by_id[relation.target_tag_id] += strength
                    related_evidence_by_id[relation.target_tag_id] = "related_tag"
                if relation.target_tag_id in query_tag_ids:
                    related_strength_by_id[relation.source_tag_id] += strength
                    related_evidence_by_id[relation.source_tag_id] = "related_tag"
            elif relation.relation_type == "parent_of":
                if relation.source_tag_id in query_tag_ids:
                    related_strength_by_id[relation.target_tag_id] += strength * 0.8
                    related_evidence_by_id[relation.target_tag_id] = "specific_tag"
                if relation.target_tag_id in query_tag_ids:
                    related_strength_by_id[relation.source_tag_id] += strength * 0.5
                    related_evidence_by_id[relation.source_tag_id] = "broad_tag"
        related_tags = self.repository.get_tags(tuple(sorted(related_strength_by_id)))
        related_strength = {
            tag.canonical_text: related_strength_by_id[tag.tag_id] for tag in related_tags
        }
        related_evidence = {
            tag.canonical_text: related_evidence_by_id[tag.tag_id] for tag in related_tags
        }
        related_hits = self.repository.search_tag_hits(
            namespace=namespace,
            canonical_tags=tuple(sorted(related_strength)),
            limit=self.candidate_limit,
        )
        for hit in related_hits:
            matched = [
                value.removeprefix("tag=")
                for value in hit.evidence
                if value.startswith("tag=")
            ]
            strength = max((related_strength.get(value, 0.0) for value in matched), default=0.0)
            if strength <= 0.0:
                continue
            scores[hit.atom_id] += hit.score * strength
            evidence[hit.atom_id].extend(
                f"{related_evidence.get(value, 'related_tag')}={value}" for value in matched
            )

        for link in self.repository.get_atom_links_touching(
            atom_ids=tuple(seed_atom_ids), relation=AtomLinkRelation.CO_USED
        ):
            strength = link.weight_raw * link.confidence
            if link.from_atom_id in seed_atom_ids:
                scores[link.to_atom_id] += strength
                evidence[link.to_atom_id].append(f"co_used_with={link.from_atom_id}")
            if link.to_atom_id in seed_atom_ids:
                scores[link.from_atom_id] += strength
                evidence[link.from_atom_id].append(f"co_used_with={link.to_atom_id}")
        for link in self.repository.get_atom_links_touching(
            atom_ids=tuple(seed_atom_ids), relation=AtomLinkRelation.ADJACENT_TO
        ):
            strength = 0.35 * link.weight_raw * link.confidence
            if link.from_atom_id in seed_atom_ids:
                scores[link.to_atom_id] += strength
                evidence[link.to_atom_id].append(f"adjacent_to={link.from_atom_id}")
            if link.to_atom_id in seed_atom_ids:
                scores[link.from_atom_id] += strength
                evidence[link.from_atom_id].append(f"adjacent_to={link.to_atom_id}")
        return dict(scores), {
            atom_id: tuple(sorted(set(values))) for atom_id, values in evidence.items()
        }

    def _query_tags(self, plan: QueryPlan) -> tuple[tuple[str, ...], list[str]]:
        tags = {normalize_tag(tag) for tag in plan.query_tags if normalize_tag(tag)}
        warnings: list[str] = []
        if self.tag_proposer:
            try:
                catalog = tuple(
                    tag.canonical_text
                    for tag in self.repository.list_tags(
                        plan.namespace, limit=self.catalog_hint_limit
                    )
                )
                proposals = self.tag_proposer.propose_tags(
                    text=plan.query,
                    namespace=plan.namespace,
                    existing_tags=catalog,
                )
                tags.update(
                    canonical
                    for proposal in proposals
                    if (canonical := normalize_tag(proposal.text))
                )
            except Exception as error:  # noqa: BLE001 - retrieval must degrade safely
                warnings.append(f"tag_proposer_unavailable:{type(error).__name__}")
        if tags and self.tag_canonicalizer is not None:
            try:
                catalog = self.repository.list_tags(
                    plan.namespace, limit=self.catalog_hint_limit
                )
                exact = {tag.canonical_text for tag in catalog}
                matches = self.tag_canonicalizer.resolve(
                    candidates=tuple(sorted(tags - exact)), catalog=catalog
                )
                tags = {
                    matches[tag].tag.canonical_text if tag in matches else tag for tag in tags
                }
            except Exception as error:  # noqa: BLE001 - retrieval must degrade safely
                warnings.append(f"tag_canonicalizer_unavailable:{type(error).__name__}")
        return tuple(sorted(tags)), warnings

    def _tag_scores(
        self, atoms: tuple[Atom, ...], atom_tags, query_tags: tuple[str, ...]
    ) -> tuple[dict[str, float], dict[str, tuple[str, ...]]]:
        if not query_tags:
            return {}, {}
        tags_by_id = (
            {tag.tag_id: tag for tag in self.repository.list_tags(atoms[0].namespace)}
            if atoms
            else {}
        )
        query_set = set(query_tags)
        edges_by_atom: dict[str, list] = defaultdict(list)
        for edge in atom_tags:
            edges_by_atom[edge.atom_id].append(edge)
        scores: dict[str, float] = {}
        evidence: dict[str, tuple[str, ...]] = {}
        for atom in atoms:
            matched: list[str] = []
            score = 0.0
            for edge in edges_by_atom.get(atom.atom_id, ()):
                tag = tags_by_id.get(edge.tag_id)
                if tag is None or tag.canonical_text not in query_set:
                    continue
                matched.append(tag.canonical_text)
                score += edge.confidence * (math.log1p(edge.weight_raw) / math.log(2.0))
            if matched:
                scores[atom.atom_id] = score / max(1, len(query_set))
                evidence[atom.atom_id] = tuple(f"tag={tag}" for tag in sorted(matched))
        return scores, evidence

    @staticmethod
    def _lexical_scores(
        atoms: tuple[Atom, ...], query: str
    ) -> tuple[dict[str, float], dict[str, tuple[str, ...]]]:
        query_terms = set(TOKEN_PATTERN.findall(query.casefold()))
        if not query_terms:
            return {}, {}
        scores: dict[str, float] = {}
        evidence: dict[str, tuple[str, ...]] = {}
        for atom in atoms:
            content_terms = set(TOKEN_PATTERN.findall(atom.content.casefold()))
            matches = sorted(query_terms.intersection(content_terms))
            if matches:
                scores[atom.atom_id] = len(matches) / len(query_terms)
                evidence[atom.atom_id] = tuple(f"lexical={term}" for term in matches[:5])
        return scores, evidence

    def _semantic_scores(
        self, atoms: tuple[Atom, ...], query: str
    ) -> tuple[dict[str, float], dict[str, tuple[str, ...]], str | None]:
        if self.embedder is None or not atoms:
            return {}, {}, None
        try:
            query_vector = self.embedder.embed_query(query)
            if not query_vector:
                return {}, {}, "semantic_query_embedding_invalid"
            embeddings = self.repository.get_embeddings(
                atom_ids=tuple(atom.atom_id for atom in atoms),
                provider=self.embedder.provider,
                model=self.embedder.model,
            )
        except Exception as error:  # noqa: BLE001 - retrieval must degrade safely
            return {}, {}, f"semantic_unavailable:{type(error).__name__}"
        scores: dict[str, float] = {}
        evidence: dict[str, tuple[str, ...]] = {}
        for atom in atoms:
            embedding = embeddings.get(atom.atom_id)
            if embedding is None or embedding.content_hash != atom.content_hash:
                continue
            similarity = max(0.0, cosine_similarity(query_vector, embedding.vector))
            if similarity > 0.0:
                scores[atom.atom_id] = similarity
                evidence[atom.atom_id] = (f"semantic={similarity:.4f}",)
        return scores, evidence, None

    def _relationship_scores(
        self,
        *,
        atoms: tuple[Atom, ...],
        namespace: str,
        query_tags: tuple[str, ...],
        seed_atom_ids: set[str],
        links,
        atom_tags,
    ) -> tuple[dict[str, float], dict[str, tuple[str, ...]]]:
        """Expand candidates over learned tag and atom relationships only."""
        scores: dict[str, float] = defaultdict(float)
        evidence: dict[str, list[str]] = defaultdict(list)
        tags = self.repository.list_tags(namespace)
        tags_by_id = {tag.tag_id: tag for tag in tags}
        query_tag_ids = {tag.tag_id for tag in tags if tag.canonical_text in set(query_tags)}

        related_tag_strength: dict[str, float] = defaultdict(float)
        for relation in self.repository.list_tag_relations(
            namespace=namespace, relation_type="co_occurs"
        ):
            if relation.weight_raw <= 0.0:
                continue
            strength = relation.weight_raw * relation.confidence
            if relation.source_tag_id in query_tag_ids:
                related_tag_strength[relation.target_tag_id] += strength
            if relation.target_tag_id in query_tag_ids:
                related_tag_strength[relation.source_tag_id] += strength

        atom_ids = {atom.atom_id for atom in atoms}
        for edge in atom_tags:
            if edge.atom_id not in atom_ids:
                continue
            strength = related_tag_strength.get(edge.tag_id, 0.0)
            if strength <= 0.0:
                continue
            scores[edge.atom_id] += strength * edge.weight_raw * edge.confidence
            tag = tags_by_id.get(edge.tag_id)
            if tag is not None:
                evidence[edge.atom_id].append(f"related_tag={tag.canonical_text}")

        for link in links:
            if link.relation is not AtomLinkRelation.CO_USED or link.weight_raw <= 0.0:
                continue
            strength = link.weight_raw * link.confidence
            if link.from_atom_id in seed_atom_ids:
                scores[link.to_atom_id] += strength
                evidence[link.to_atom_id].append(f"co_used_with={link.from_atom_id}")
            if link.to_atom_id in seed_atom_ids:
                scores[link.from_atom_id] += strength
                evidence[link.from_atom_id].append(f"co_used_with={link.to_atom_id}")

        return dict(scores), {
            atom_id: tuple(sorted(set(values))) for atom_id, values in evidence.items()
        }

    @staticmethod
    def _normalize(scores: dict[str, float]) -> dict[str, float]:
        maximum = max(scores.values(), default=0.0)
        if maximum <= 0.0:
            return {}
        return {atom_id: min(1.0, score / maximum) for atom_id, score in scores.items()}

    @staticmethod
    def _channel_weights(*, has_tags: bool, has_semantic: bool) -> dict[str, float]:
        raw = {
            "tag": 0.45 if has_tags else 0.0,
            "lexical": 0.25,
            "semantic": 0.30 if has_semantic else 0.0,
        }
        total = sum(raw.values())
        return {channel: weight / total for channel, weight in raw.items()}

    @staticmethod
    def _summary_lineage(links) -> dict[str, tuple[str, ...]]:
        lineage: dict[str, list[str]] = defaultdict(list)
        for link in links:
            if link.relation in {
                AtomLinkRelation.SUMMARIZES,
                AtomLinkRelation.DERIVED_FROM,
            }:
                lineage[link.from_atom_id].append(link.to_atom_id)
        return {atom_id: tuple(sorted(set(targets))) for atom_id, targets in lineage.items()}

    @staticmethod
    def _sort_key(item: RetrievalItem) -> tuple[float, float, str]:
        timestamp = item.occurred_at.timestamp() if item.occurred_at else 0.0
        return item.score.final, timestamp, item.atom_id

    @staticmethod
    def _pack_without_duplicate_summaries(
        ranked: list[RetrievalItem], top_k: int
    ) -> list[RetrievalItem]:
        selected: list[RetrievalItem] = []
        selected_source_ids: set[str] = set()
        for item in ranked:
            if item.kind is AtomKind.TEMPORAL_SUMMARY and item.lineage_atom_ids:
                if set(item.lineage_atom_ids).issubset(selected_source_ids):
                    continue
            selected.append(item)
            if item.kind is AtomKind.SOURCE:
                selected_source_ids.add(item.atom_id)
            if len(selected) >= top_k:
                break
        return selected

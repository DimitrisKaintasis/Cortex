from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from data_retrieval.benchmarks.longmemeval import (
    ImportedLongMemEvalCase,
    LongMemEvalIngestService,
)
from data_retrieval.benchmarks.longmemeval_ablation import (
    FrozenQueryFeatures,
    RetrievalProfile,
)
from data_retrieval.benchmarks.longmemeval_pipeline import LongMemEvalPipelineRunner
from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    AtomLinkRelation,
    WeightEventSource,
)
from data_retrieval.retrieval.embedding import Embedder
from data_retrieval.retrieval.models import FeedbackRequest, RetrievalChannels
from data_retrieval.services.learning import LearningService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.repository import Repository

EXPERIENCE_PROFILES = (
    RetrievalProfile(
        "tags_only",
        "direct canonical tag matches",
        RetrievalChannels(
            tags=True,
            lexical=False,
            semantic=False,
            relationships=False,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "graph_only",
        "direct tags plus learned and admitted graph traversal",
        RetrievalChannels(
            tags=True,
            lexical=False,
            semantic=False,
            relationships=True,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "vector_only",
        "frozen semantic retrieval without graph traversal",
        RetrievalChannels(
            tags=False,
            lexical=False,
            semantic=True,
            relationships=False,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "hybrid_graph",
        "tags, lexical, frozen vectors, and admitted graph traversal",
        RetrievalChannels(
            tags=True,
            lexical=True,
            semantic=True,
            relationships=True,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
)


@dataclass(frozen=True, slots=True)
class ExperienceRound:
    round_number: int
    feedback_events: int
    cases_without_reward: int
    selected_items: int
    mem0_assisted_events: int
    atom_tag_updates: int
    atom_link_updates: int
    tag_relation_updates: int
    cases: tuple[dict[str, Any], ...]


@dataclass(frozen=True, slots=True)
class ExperienceReport:
    schema_version: int
    suite_id: str
    dataset_id: str
    dataset_hash: str
    question_ids: tuple[str, ...]
    feedback_selection: str
    usage_round_count: int
    top_k: int
    embedding_provider: str
    embedding_model: str
    query_feature_path: str
    snapshots: tuple[dict[str, Any], ...]
    usage_rounds: tuple[ExperienceRound, ...]
    metric_deltas: dict[str, dict[str, float]]
    mechanical_passed: bool
    learning_signal_passed: bool
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "passed": self.mechanical_passed,
            "snapshots": list(self.snapshots),
            "usage_rounds": [asdict(item) for item in self.usage_rounds],
        }


class Mem0ExperienceSuite:
    """Measure retrieval before and after attributable outcome learning."""

    def __init__(self, repository: Repository, *, embedder: Embedder) -> None:
        self.repository = repository
        self.embedder = embedder

    def run(
        self,
        *,
        suite_id: str,
        dataset_path: Path,
        dataset_id: str,
        query_feature_path: Path,
        namespace_prefix: str,
        question_ids: tuple[str, ...],
        feedback_selection: str,
        usage_round_count: int = 5,
        top_k: int = 10,
    ) -> ExperienceReport:
        if feedback_selection not in {"first_relevant", "all_relevant"}:
            raise ValueError("feedback_selection must be first_relevant or all_relevant")
        if usage_round_count <= 0:
            raise ValueError("usage_round_count must be positive")
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if (
            not suite_id.strip()
            or not question_ids
            or any(not value.strip() for value in question_ids)
            or len(set(question_ids)) != len(question_ids)
        ):
            raise ValueError("suite_id and question_ids must be non-empty and unique")

        imported = LongMemEvalIngestService(self.repository).ingest_path(
            path=dataset_path,
            dataset_id=dataset_id,
            namespace_prefix=namespace_prefix,
            question_ids=question_ids,
        )
        features = _load_query_features(
            query_feature_path,
            dataset_id=imported.dataset_id,
            dataset_hash=imported.dataset_hash,
            cases=imported.cases,
            embedder=self.embedder,
        )
        namespaces = tuple(case.namespace for case in imported.cases)
        proposal_count = sum(
            len(
                self.repository.list_atom_links(
                    namespace=namespace,
                    relation=AtomLinkRelation.MEM0_ENTITY_RELATION,
                )
            )
            for namespace in namespaces
        )
        if proposal_count == 0:
            raise ValueError("benchmark requires a prepared Mem0 entity graph")

        snapshots: list[dict[str, Any]] = []
        rounds: list[ExperienceRound] = []
        current = self._snapshot(
            name="cold",
            imported=imported.cases,
            dataset_path=dataset_path,
            dataset_id=dataset_id,
            namespace_prefix=namespace_prefix,
            features=features,
            top_k=top_k,
        )
        snapshots.append(current)
        for round_number in range(1, usage_round_count + 1):
            round_result = self._apply_round(
                suite_id=suite_id,
                round_number=round_number,
                cases=imported.cases,
                hybrid=current["retrieval"]["hybrid_graph"],
                feedback_selection=feedback_selection,
            )
            rounds.append(round_result)
            current = self._snapshot(
                name=f"after_usage_round_{round_number}",
                imported=imported.cases,
                dataset_path=dataset_path,
                dataset_id=dataset_id,
                namespace_prefix=namespace_prefix,
                features=features,
                top_k=top_k,
            )
            snapshots.append(current)

        audits = tuple(
            WeightLedgerService(self.repository).audit_namespace(namespace)
            for namespace in namespaces
        )
        deltas = _metric_deltas(snapshots[0], snapshots[-1])
        hybrid_delta = deltas["hybrid_graph"]
        mechanical_passed = (
            all(audit.passed for audit in audits)
            and sum(item.feedback_events for item in rounds) > 0
            and len(snapshots) == usage_round_count + 1
        )
        learning_signal_passed = (
            hybrid_delta["turn_mean_reciprocal_rank"] > 0.0
            and hybrid_delta["turn_recall_at_k"] >= 0.0
            and hybrid_delta["direct_turn_recall_at_k"] >= 0.0
            and hybrid_delta["useful_context_fraction"] >= 0.0
        )
        return ExperienceReport(
            schema_version=1,
            suite_id=suite_id,
            dataset_id=imported.dataset_id,
            dataset_hash=imported.dataset_hash,
            question_ids=tuple(case.question_id for case in imported.cases),
            feedback_selection=feedback_selection,
            usage_round_count=usage_round_count,
            top_k=top_k,
            embedding_provider=self.embedder.provider,
            embedding_model=self.embedder.model,
            query_feature_path=str(query_feature_path),
            snapshots=tuple(snapshots),
            usage_rounds=tuple(rounds),
            metric_deltas=deltas,
            mechanical_passed=mechanical_passed,
            learning_signal_passed=learning_signal_passed,
            limitations=(
                "This is supervised adaptation using public benchmark evidence labels.",
                "The same development questions are reused across rounds; this is not "
                "held-out generalization.",
                "Positive feedback measures useful-evidence reinforcement, not answer generation.",
                "Mem0 relationship proposals remain factual inputs; feedback updates learned "
                "behavioral edges.",
            ),
        )

    def _snapshot(
        self,
        *,
        name: str,
        imported: tuple[ImportedLongMemEvalCase, ...],
        dataset_path: Path,
        dataset_id: str,
        namespace_prefix: str,
        features: dict[str, FrozenQueryFeatures],
        top_k: int,
    ) -> dict[str, Any]:
        query_tags = {key: value.query_tags for key, value in features.items()}
        query_vectors = {key: value.query_vector for key, value in features.items()}
        question_ids = tuple(case.question_id for case in imported)
        pipeline = LongMemEvalPipelineRunner(self.repository, embedder=self.embedder)
        retrieval: dict[str, Any] = {}
        for profile in EXPERIENCE_PROFILES:
            report = pipeline.run(
                dataset_path=dataset_path,
                dataset_id=dataset_id,
                namespace_prefix=namespace_prefix,
                question_ids=question_ids,
                top_k=top_k,
                retrieval_channels=profile.channels,
                query_tags_by_question=query_tags,
                query_vectors_by_question=query_vectors,
            )
            payload = report.as_dict()
            payload["experience_metrics"] = self._experience_metrics(imported, payload["cases"])
            retrieval[profile.name] = {
                "description": profile.description,
                **payload,
            }
        return {
            "name": name,
            "graph": self._graph_stats(tuple(case.namespace for case in imported)),
            "retrieval": retrieval,
        }

    def _apply_round(
        self,
        *,
        suite_id: str,
        round_number: int,
        cases: tuple[ImportedLongMemEvalCase, ...],
        hybrid: dict[str, Any],
        feedback_selection: str,
    ) -> ExperienceRound:
        case_specs = {case.question_id: case for case in cases}
        counters: Counter[str] = Counter()
        details: list[dict[str, Any]] = []
        for result in hybrid["cases"]:
            case = case_specs[str(result["question_id"])]
            relevant = self._relevant_result_indexes(case, result)
            selected_indexes = relevant[:1] if feedback_selection == "first_relevant" else relevant
            if not selected_indexes:
                counters["cases_without_reward"] += 1
                details.append(
                    {"question_id": case.question_id, "selected_atom_ids": [], "rewarded": False}
                )
                continue
            selected_atom_ids = tuple(
                dict.fromkeys(result["retrieved_atom_ids"][index] for index in selected_indexes)
            )
            score_rows = result["retrieved_scores"]
            used_mem0 = any(
                any(
                    str(value).startswith("mem0_entity_") for value in score_rows[index]["evidence"]
                )
                for index in selected_indexes
            )
            learned = LearningService(self.repository).apply_feedback(
                FeedbackRequest(
                    feedback_id=stable_id(
                        "mem0-experience-feedback",
                        suite_id,
                        feedback_selection,
                        str(round_number),
                        case.question_id,
                    ),
                    retrieval_id=str(result["retrieval_id"]),
                    selected_atom_ids=selected_atom_ids,
                    outcome="positive",
                    reason="public benchmark evidence selected as useful",
                    used_mem0=used_mem0,
                )
            )
            counters["feedback_events"] += 1
            counters["selected_items"] += len(selected_atom_ids)
            counters["mem0_assisted_events"] += int(used_mem0)
            counters["atom_tag_updates"] += learned.atom_tag_updates
            counters["atom_link_updates"] += learned.atom_link_updates
            counters["tag_relation_updates"] += learned.tag_relation_updates
            details.append(
                {
                    "question_id": case.question_id,
                    "selected_atom_ids": list(selected_atom_ids),
                    "rewarded": True,
                    "used_mem0": used_mem0,
                    "learning_multiplier": learned.learning_multiplier,
                }
            )
        return ExperienceRound(
            round_number=round_number,
            feedback_events=counters["feedback_events"],
            cases_without_reward=counters["cases_without_reward"],
            selected_items=counters["selected_items"],
            mem0_assisted_events=counters["mem0_assisted_events"],
            atom_tag_updates=counters["atom_tag_updates"],
            atom_link_updates=counters["atom_link_updates"],
            tag_relation_updates=counters["tag_relation_updates"],
            cases=tuple(details),
        )

    def _relevant_result_indexes(
        self, case: ImportedLongMemEvalCase, result: dict[str, Any]
    ) -> tuple[int, ...]:
        evidence = set(case.evidence_atom_ids)
        answer_sessions = set(case.answer_session_ids)
        source_rows = result["retrieved_source_atom_ids"]
        source_ids = tuple(dict.fromkeys(atom_id for row in source_rows for atom_id in row))
        source_atoms = {atom.atom_id: atom for atom in self.repository.get_atoms(source_ids)}
        relevant: list[int] = []
        covered_evidence: set[str] = set()
        covered_sessions: set[str] = set()
        for index, row in enumerate(source_rows):
            matched_evidence = evidence.intersection(row)
            matched_sessions = answer_sessions.intersection(
                {
                    str(source_atoms[atom_id].metadata.get("session_id"))
                    for atom_id in row
                    if atom_id in source_atoms
                }
            )
            if matched_evidence.difference(covered_evidence) or matched_sessions.difference(
                covered_sessions
            ):
                relevant.append(index)
                covered_evidence.update(matched_evidence)
                covered_sessions.update(matched_sessions)
        return tuple(relevant)

    def _experience_metrics(
        self,
        cases: tuple[ImportedLongMemEvalCase, ...],
        results: list[dict[str, Any]],
    ) -> dict[str, float | int]:
        case_specs = {case.question_id: case for case in cases}
        returned_count = 0
        useful_count = 0
        relationship_assisted_count = 0
        relationship_score_total = 0.0
        for result in results:
            returned_count += len(result["retrieved_atom_ids"])
            useful_count += len(
                self._relevant_result_indexes(case_specs[str(result["question_id"])], result)
            )
            for score in result["retrieved_scores"]:
                relationship_score_total += float(score["relationship"])
                relationship_assisted_count += int(float(score["relationship"]) > 0.0)
        return {
            "returned_item_count": returned_count,
            "distinct_useful_item_count": useful_count,
            "useful_context_fraction": round(
                useful_count / returned_count if returned_count else 0.0, 12
            ),
            "relationship_assisted_item_count": relationship_assisted_count,
            "mean_relationship_score": round(
                relationship_score_total / returned_count if returned_count else 0.0,
                12,
            ),
        }

    def _graph_stats(self, namespaces: tuple[str, ...]) -> dict[str, Any]:
        atoms = tuple(
            atom
            for namespace in namespaces
            for atom in self.repository.list_atoms(namespace=namespace)
        )
        atom_tags = tuple(
            edge for namespace in namespaces for edge in self.repository.list_atom_tags(namespace)
        )
        tag_relations = tuple(
            edge
            for namespace in namespaces
            for edge in self.repository.list_tag_relations(namespace=namespace)
        )
        links = tuple(
            link
            for namespace in namespaces
            for link in self.repository.list_atom_links(namespace=namespace)
        )
        mem0_links = tuple(
            link for link in links if link.relation is AtomLinkRelation.MEM0_ENTITY_RELATION
        )
        feedback_events = tuple(
            event
            for namespace in namespaces
            for event in self.repository.list_weight_events(namespace=namespace)
            if event.source_type is WeightEventSource.FEEDBACK
        )
        edge_transition_counts = Counter(
            (event.target_type.value, event.target_id, event.related_id, event.relation_type)
            for event in feedback_events
        )
        return {
            "atom_count": len(atoms),
            "mem0_entity_atom_count": sum(
                atom.metadata.get("source_system") == "mem0" for atom in atoms
            ),
            "atom_tag_edge_count": len(atom_tags),
            "tag_relation_count": len(tag_relations),
            "atom_link_counts": dict(
                sorted(Counter(link.relation.value for link in links).items())
            ),
            "mem0_admission_counts": dict(
                sorted(
                    Counter(
                        str(link.metadata.get("admission_state", "unknown")) for link in mem0_links
                    ).items()
                )
            ),
            "mem0_active_weight": round(sum(link.weight_raw for link in mem0_links), 6),
            "co_used_weight": round(
                sum(link.weight_raw for link in links if link.relation is AtomLinkRelation.CO_USED),
                6,
            ),
            "tag_relation_weight": round(sum(edge.weight_raw for edge in tag_relations), 6),
            "atom_tag_weight": round(sum(edge.weight_raw for edge in atom_tags), 6),
            "feedback_transition_count": len(feedback_events),
            "feedback_transition_targets": dict(
                sorted(Counter(event.target_type.value for event in feedback_events).items())
            ),
            "feedback_distinct_edges": len(edge_transition_counts),
            "feedback_max_transitions_per_edge": max(edge_transition_counts.values(), default=0),
        }


def _load_query_features(
    path: Path,
    *,
    dataset_id: str,
    dataset_hash: str,
    cases: tuple[ImportedLongMemEvalCase, ...],
    embedder: Embedder,
) -> dict[str, FrozenQueryFeatures]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("query feature cache must contain one JSON object")
    expected = {
        "schema_version": 1,
        "dataset_id": dataset_id,
        "dataset_hash": dataset_hash,
        "embedding_provider": embedder.provider,
        "embedding_model": embedder.model,
    }
    mismatches = [key for key, value in expected.items() if payload.get(key) != value]
    if mismatches:
        raise ValueError("query feature cache identity mismatch: " + ", ".join(mismatches))
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list):
        raise ValueError("query feature cache cases must be an array")
    raw_by_id = {str(item.get("question_id")): item for item in raw_cases if isinstance(item, dict)}
    features: dict[str, FrozenQueryFeatures] = {}
    for case in cases:
        raw = raw_by_id.get(case.question_id)
        if raw is None:
            raise ValueError(f"query feature cache is missing case: {case.question_id}")
        feature = FrozenQueryFeatures(
            question_id=case.question_id,
            namespace=str(raw["namespace"]),
            question_hash=str(raw["question_hash"]),
            query_tags=tuple(str(value) for value in raw.get("query_tags", ())),
            query_vector=tuple(float(value) for value in raw["query_vector"]),
            warnings=tuple(str(value) for value in raw.get("warnings", ())),
        )
        if feature.namespace != case.namespace or feature.question_hash != content_hash(
            case.question
        ):
            raise ValueError(f"query feature cache case identity mismatch: {case.question_id}")
        features[case.question_id] = feature
    return features


def _metric_deltas(first: dict[str, Any], last: dict[str, Any]) -> dict[str, dict[str, float]]:
    metrics = (
        "turn_recall_at_k",
        "turn_mean_reciprocal_rank",
        "direct_turn_recall_at_k",
        "direct_turn_mean_reciprocal_rank",
    )
    deltas = {
        profile.name: {
            metric: round(
                float(last["retrieval"][profile.name][metric])
                - float(first["retrieval"][profile.name][metric]),
                12,
            )
            for metric in metrics
        }
        for profile in EXPERIENCE_PROFILES
    }
    for profile in EXPERIENCE_PROFILES:
        deltas[profile.name]["useful_context_fraction"] = round(
            float(last["retrieval"][profile.name]["experience_metrics"]["useful_context_fraction"])
            - float(
                first["retrieval"][profile.name]["experience_metrics"]["useful_context_fraction"]
            ),
            12,
        )
    return deltas

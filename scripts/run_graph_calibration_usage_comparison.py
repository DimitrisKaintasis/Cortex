from __future__ import annotations

import argparse
import json
import sqlite3
import statistics
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_retrieval.benchmarks.longmemeval import LongMemEvalIngestService
from data_retrieval.benchmarks.longmemeval_pipeline import LongMemEvalPipelineRunner
from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import AtomRole
from data_retrieval.retrieval.models import (
    FeedbackRequest,
    QueryPlan,
    RetrievalChannels,
    RetrievalItem,
    TemporalMode,
)
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.sqlite import SQLiteRepository

DEFAULT_QUESTION_IDS = (
    "gpt4_2655b836",
    "6d550036",
    "6aeb4375",
    "75832dbd",
    "c4f10528",
    "e47becba",
)

PROFILES = {
    "tags_only": RetrievalChannels(
        tags=True,
        lexical=False,
        semantic=False,
        relationships=False,
        temporal=False,
        temporal_summaries=False,
    ),
    "graph_only": RetrievalChannels(
        tags=True,
        lexical=False,
        semantic=False,
        relationships=True,
        temporal=False,
        temporal_summaries=False,
    ),
    "vector_only": RetrievalChannels(
        tags=False,
        lexical=False,
        semantic=True,
        relationships=False,
        temporal=False,
        temporal_summaries=False,
    ),
    "direct_hybrid": RetrievalChannels(
        tags=True,
        lexical=True,
        semantic=True,
        relationships=True,
        temporal=False,
        temporal_summaries=False,
    ),
}


@dataclass(frozen=True, slots=True)
class FrozenEmbedder:
    provider: str
    model: str

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        raise RuntimeError("the comparison requires precomputed document embeddings")

    def embed_query(self, text: str) -> tuple[float, ...]:
        raise RuntimeError("the comparison requires frozen query vectors")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Compare graph retrieval before calibration and after controlled use."
    )
    parser.add_argument("--baseline-db", type=Path, required=True)
    parser.add_argument("--calibrated-db", type=Path, required=True)
    parser.add_argument("--used-db", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--query-features", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--dataset-id", default="longmemeval-dev20-v1")
    parser.add_argument("--namespace-prefix", default="longmemeval-dev20-v1")
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument("--usage-rounds", type=int, default=3)
    parser.add_argument(
        "--feedback-mode",
        choices=("positive-only", "corrective"),
        default="corrective",
    )
    parser.add_argument("--question-id", action="append", dest="question_ids")
    args = parser.parse_args()
    question_ids = tuple(args.question_ids or DEFAULT_QUESTION_IDS)
    if args.used_db.exists():
        parser.error(f"used database already exists: {args.used_db}")

    feature_payload = json.loads(args.query_features.read_text(encoding="utf-8"))
    features = {
        item["question_id"]: {
            "namespace": str(item["namespace"]),
            "tags": tuple(str(tag) for tag in item["query_tags"]),
            "vector": tuple(float(value) for value in item["query_vector"]),
        }
        for item in feature_payload["cases"]
        if item["question_id"] in question_ids
    }
    if set(features) != set(question_ids):
        missing = sorted(set(question_ids) - set(features))
        parser.error(f"query feature cache is missing: {missing}")
    embedder = FrozenEmbedder(
        provider=str(feature_payload["embedding_provider"]),
        model=str(feature_payload["embedding_model"]),
    )

    snapshots: list[dict[str, Any]] = []
    with SQLiteRepository(args.baseline_db) as repository:
        snapshots.append(
            _snapshot(
                "before_joint_calibration",
                repository,
                args=args,
                question_ids=question_ids,
                features=features,
                embedder=embedder,
            )
        )
    with SQLiteRepository(args.calibrated_db) as repository:
        snapshots.append(
            _snapshot(
                "after_joint_calibration",
                repository,
                args=args,
                question_ids=question_ids,
                features=features,
                embedder=embedder,
            )
        )

    _backup_database(args.calibrated_db, args.used_db)
    usage_reports: list[dict[str, Any]] = []
    with SQLiteRepository(args.used_db) as repository:
        imported = LongMemEvalIngestService(repository).ingest_path(
            path=args.dataset,
            namespace_prefix=args.namespace_prefix,
            dataset_id=args.dataset_id,
            question_ids=question_ids,
        )
        cases = {case.question_id: case for case in imported.cases}
        for round_number in range(1, args.usage_rounds + 1):
            usage_reports.append(
                _apply_usage_round(
                    repository,
                    round_number=round_number,
                    cases=cases,
                    question_ids=question_ids,
                    features=features,
                    embedder=embedder,
                    top_k=args.top_k,
                    feedback_mode=args.feedback_mode,
                )
            )
            snapshots.append(
                _snapshot(
                    f"after_usage_round_{round_number}",
                    repository,
                    args=args,
                    question_ids=question_ids,
                    features=features,
                    embedder=embedder,
                )
            )

    output = {
        "schema_version": 1,
        "dataset_id": args.dataset_id,
        "question_ids": list(question_ids),
        "top_k": args.top_k,
        "usage_method": (
            "Frozen hybrid retrieval exposes candidates. Public benchmark evidence labels "
            "reward the first relevant returned item"
            + (
                " and penalize the first irrelevant item"
                if args.feedback_mode == "corrective"
                else ""
            )
            + ". This measures supervised adaptation, not held-out generalization."
        ),
        "feedback_mode": args.feedback_mode,
        "snapshots": snapshots,
        "usage_rounds": usage_reports,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8", newline="\n")
    print(json.dumps(_compact_summary(output), indent=2))
    return 0


def _snapshot(
    name: str,
    repository: SQLiteRepository,
    *,
    args: argparse.Namespace,
    question_ids: tuple[str, ...],
    features: dict[str, dict[str, Any]],
    embedder: FrozenEmbedder,
) -> dict[str, Any]:
    query_tags = {
        question_id: tuple(features[question_id]["tags"])
        for question_id in question_ids
    }
    query_vectors = {
        question_id: tuple(features[question_id]["vector"])
        for question_id in question_ids
    }
    runner = LongMemEvalPipelineRunner(repository, embedder=embedder)
    evaluations: dict[str, Any] = {}
    for profile_name, channels in PROFILES.items():
        report = runner.run(
            dataset_path=args.dataset,
            dataset_id=args.dataset_id,
            namespace_prefix=args.namespace_prefix,
            question_ids=question_ids,
            top_k=args.top_k,
            retrieval_channels=channels,
            query_tags_by_question=query_tags,
            query_vectors_by_question=query_vectors,
        )
        evaluations[profile_name] = report.as_dict(include_cases=False)
    namespaces = tuple(
        str(features[question_id]["namespace"]) for question_id in question_ids
    )
    return {
        "name": name,
        "graph": _graph_stats(repository, namespaces),
        "retrieval": evaluations,
    }


def _apply_usage_round(
    repository: SQLiteRepository,
    *,
    round_number: int,
    cases: dict[str, Any],
    question_ids: tuple[str, ...],
    features: dict[str, dict[str, Any]],
    embedder: FrozenEmbedder,
    top_k: int,
    feedback_mode: str,
) -> dict[str, Any]:
    service = RetrievalService(
        repository,
        embedder=embedder,
        channels=PROFILES["direct_hybrid"],
    )
    learning = LearningService(repository)
    counters: Counter[str] = Counter()
    per_case: list[dict[str, Any]] = []
    for question_id in question_ids:
        case = cases[question_id]
        feature = features[question_id]
        result = service.retrieve(
            QueryPlan(
                query=case.question,
                namespace=case.namespace,
                query_tags=tuple(feature["tags"]),
                query_vector=tuple(feature["vector"]),
                top_k=top_k,
                timeline_id=case.question_id,
                temporal_mode=TemporalMode.NONE,
                reference_time=case.question_date,
            )
        )
        relevance = {
            item.atom_id: _is_relevant(repository, item, case) for item in result.items
        }
        positive = next((item for item in result.items if relevance[item.atom_id]), None)
        negative = next((item for item in result.items if not relevance[item.atom_id]), None)
        case_result: dict[str, Any] = {
            "question_id": question_id,
            "positive_atom_id": positive.atom_id if positive else None,
            "negative_atom_id": negative.atom_id if negative else None,
        }
        feedback_items = [("positive", positive)]
        if feedback_mode == "corrective":
            feedback_items.append(("negative", negative))
        for outcome, item in feedback_items:
            if item is None:
                counters[f"{outcome}_missing"] += 1
                continue
            atom = repository.get_atom(item.atom_id)
            feedback = learning.apply_feedback(
                FeedbackRequest(
                    feedback_id=stable_id(
                        "graph-use-comparison",
                        str(round_number),
                        question_id,
                        outcome,
                    ),
                    retrieval_id=result.retrieval_id,
                    selected_atom_ids=(item.atom_id,),
                    outcome=outcome,
                    reason="public benchmark-labelled controlled usage simulation",
                    used_mem0=(
                        atom is not None
                        and atom.role is AtomRole.DERIVED
                        and atom.metadata.get("source_system") == "mem0"
                    ),
                )
            )
            counters[f"{outcome}_events"] += 1
            counters["atom_tag_updates"] += feedback.atom_tag_updates
            counters["atom_link_updates"] += feedback.atom_link_updates
            counters["tag_relation_updates"] += feedback.tag_relation_updates
            case_result[f"{outcome}_learning_multiplier"] = feedback.learning_multiplier
        per_case.append(case_result)
    return {"round": round_number, "counts": dict(counters), "cases": per_case}


def _is_relevant(
    repository: SQLiteRepository,
    item: RetrievalItem,
    case: Any,
) -> bool:
    source_ids = item.lineage_atom_ids or (item.atom_id,)
    if set(source_ids).intersection(case.evidence_atom_ids):
        return True
    answer_sessions = set(case.answer_session_ids)
    return any(
        str(atom.metadata.get("session_id")) in answer_sessions
        for atom in repository.get_atoms(tuple(source_ids))
    )


def _graph_stats(
    repository: SQLiteRepository, namespaces: tuple[str, ...]
) -> dict[str, Any]:
    relations = tuple(
        relation
        for namespace in namespaces
        for relation in repository.list_tag_relations(namespace=namespace)
    )
    atom_tags = tuple(
        edge
        for namespace in namespaces
        for edge in repository.list_atom_tags(namespace)
    )
    atoms = tuple(
        atom
        for namespace in namespaces
        for atom in repository.list_atoms(namespace=namespace)
    )
    events = tuple(
        event
        for namespace in namespaces
        for event in repository.list_weight_events(namespace=namespace)
    )
    return {
        "atom_count": len(atoms),
        "mem0_atom_count": sum(
            atom.role is AtomRole.DERIVED
            and atom.metadata.get("source_system") == "mem0"
            for atom in atoms
        ),
        "atom_tag_count": len(atom_tags),
        "tag_relation_count": len(relations),
        "atom_tag_weights": _distribution(tuple(edge.weight_raw for edge in atom_tags)),
        "tag_relation_weights": _distribution(
            tuple(edge.weight_raw for edge in relations)
        ),
        "weight_event_counts": dict(
            Counter(event.source_type.value for event in events)
        ),
        "joint_calibration_event_count": sum(
            "mem0-joint-bootstrap-v1" in event.policy_version for event in events
        ),
    }


def _distribution(values: tuple[float, ...]) -> dict[str, float]:
    if not values:
        return {"mean": 0.0, "median": 0.0, "p95": 0.0, "maximum": 0.0}
    ordered = sorted(values)
    p95_index = min(len(ordered) - 1, math_ceil(0.95 * len(ordered)) - 1)
    return {
        "mean": statistics.fmean(ordered),
        "median": statistics.median(ordered),
        "p95": ordered[p95_index],
        "maximum": ordered[-1],
    }


def math_ceil(value: float) -> int:
    integer = int(value)
    return integer if value == integer else integer + 1


def _backup_database(source: Path, target: Path) -> None:
    source_connection = sqlite3.connect(source)
    target_connection = sqlite3.connect(target)
    try:
        source_connection.backup(target_connection)
    finally:
        target_connection.close()
        source_connection.close()


def _compact_summary(output: dict[str, Any]) -> dict[str, Any]:
    snapshots = []
    for snapshot in output["snapshots"]:
        snapshots.append(
            {
                "name": snapshot["name"],
                "mem0_atoms": snapshot["graph"]["mem0_atom_count"],
                "relations": snapshot["graph"]["tag_relation_count"],
                "feedback_events": snapshot["graph"]["weight_event_counts"].get(
                    "feedback", 0
                ),
                "graph_turn_recall": snapshot["retrieval"]["graph_only"][
                    "direct_turn_recall_at_k"
                ],
                "graph_turn_mrr": snapshot["retrieval"]["graph_only"][
                    "direct_turn_mean_reciprocal_rank"
                ],
                "hybrid_turn_recall": snapshot["retrieval"]["direct_hybrid"][
                    "direct_turn_recall_at_k"
                ],
                "hybrid_turn_mrr": snapshot["retrieval"]["direct_hybrid"][
                    "direct_turn_mean_reciprocal_rank"
                ],
            }
        )
    return {"question_ids": output["question_ids"], "snapshots": snapshots}


if __name__ == "__main__":
    raise SystemExit(main())

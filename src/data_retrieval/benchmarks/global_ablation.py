from __future__ import annotations

import time
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from data_retrieval.benchmarks.longmemeval import LongMemEvalIngestService, iter_longmemeval_cases
from data_retrieval.retrieval.embedding import Embedder
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class CaseVariantResult:
    variant: str
    session_hit: bool
    session_recall: float
    turn_hit: bool
    turn_mrr: float
    retrieved_atom_ids: tuple[str, ...]
    latency_ms: float


@dataclass(frozen=True, slots=True)
class CaseAblationResult:
    question_id: str
    question_type: str
    question: str
    answer: str
    answer_session_ids: tuple[str, ...]
    evidence_atom_ids: tuple[str, ...]
    variants: dict[str, CaseVariantResult]


@dataclass(frozen=True, slots=True)
class VariantSummaryMetrics:
    case_count: int
    session_hit_at_k: float
    session_recall_at_k: float
    turn_hit_at_k: float
    mean_reciprocal_rank: float
    avg_latency_ms: float


@dataclass(frozen=True, slots=True)
class GlobalAblationReport:
    dataset_id: str
    dataset_hash: str
    total_sessions_in_namespace: int
    total_atoms_in_namespace: int
    case_count: int
    top_k: int
    variant_summaries: dict[str, VariantSummaryMetrics]
    cases: tuple[CaseAblationResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "dataset_id": self.dataset_id,
            "dataset_hash": self.dataset_hash,
            "total_sessions_in_namespace": self.total_sessions_in_namespace,
            "total_atoms_in_namespace": self.total_atoms_in_namespace,
            "case_count": self.case_count,
            "top_k": self.top_k,
            "variant_summaries": {
                variant: asdict(summary)
                for variant, summary in self.variant_summaries.items()
            },
            "cases": [
                {
                    "question_id": case.question_id,
                    "question_type": case.question_type,
                    "question": case.question,
                    "answer": case.answer,
                    "answer_session_ids": list(case.answer_session_ids),
                    "evidence_atom_ids": list(case.evidence_atom_ids),
                    "variants": {
                        v_name: asdict(v_res)
                        for v_name, v_res in case.variants.items()
                    },
                }
                for case in self.cases
            ],
        }


class GlobalAblationRunner:
    """Evaluates core atom retrieval capabilities in a single shared global namespace."""

    def __init__(
        self,
        repository: Repository,
        *,
        embedder: Embedder | None = None,
    ) -> None:
        self.repository = repository
        self.embedder = embedder
        self.retrieval_service = RetrievalService(repository, embedder=embedder)

    def run(
        self,
        *,
        dataset_path: Path,
        dataset_id: str = "oracle-global",
        timezone_name: str = "UTC",
        max_cases: int | None = None,
        question_ids: tuple[str, ...] | None = None,
        top_k: int = 10,
    ) -> GlobalAblationReport:
        # Ingest into single shared global namespace
        ingest_service = LongMemEvalIngestService(self.repository)
        import_result = ingest_service.ingest_path(
            path=dataset_path,
            dataset_id=dataset_id,
            timezone_name=timezone_name,
            max_cases=max_cases,
            question_ids=question_ids,
            use_global_namespace=True,
        )

        global_namespace = import_result.cases[0].namespace
        case_map = {c.question_id: c for c in import_result.cases}

        evaluated_cases: list[CaseAblationResult] = []
        raw_cases = iter_longmemeval_cases(dataset_path, timezone_name=timezone_name)

        variants_to_test = ["variant_0_lexical", "variant_2_atom_engine"]
        if self.embedder is not None:
            variants_to_test.insert(1, "variant_1_vector")

        variant_accumulators: dict[str, dict[str, float]] = {
            v: {
                "session_hits": 0.0,
                "session_recalls": 0.0,
                "turn_hits": 0.0,
                "turn_mrrs": 0.0,
                "latencies": 0.0,
            }
            for v in variants_to_test
        }

        for raw_case in raw_cases:
            if raw_case.question_id not in case_map:
                continue

            imported_case = case_map[raw_case.question_id]
            target_sessions = set(imported_case.answer_session_ids)
            target_turns = set(imported_case.evidence_atom_ids)

            case_variants: dict[str, CaseVariantResult] = {}

            for variant in variants_to_test:
                start_time = time.perf_counter()
                retrieved_atoms = self._execute_retrieval(
                    query=raw_case.question,
                    namespace=global_namespace,
                    variant=variant,
                    top_k=top_k,
                )
                elapsed_ms = (time.perf_counter() - start_time) * 1000.0

                retrieved_atom_ids = tuple(atom.atom_id for atom in retrieved_atoms)
                retrieved_session_ids = [
                    atom.metadata.get("session_id")
                    for atom in retrieved_atoms
                    if "session_id" in atom.metadata
                ]

                # Session metrics
                matching_sessions = target_sessions.intersection(retrieved_session_ids)
                session_hit = len(matching_sessions) > 0
                session_recall = (
                    len(matching_sessions) / len(target_sessions) if target_sessions else 0.0
                )

                # Turn metrics
                turn_hit = False
                turn_mrr = 0.0
                for rank, atom_id in enumerate(retrieved_atom_ids, start=1):
                    if atom_id in target_turns:
                        turn_hit = True
                        turn_mrr = 1.0 / rank
                        break

                case_variants[variant] = CaseVariantResult(
                    variant=variant,
                    session_hit=session_hit,
                    session_recall=session_recall,
                    turn_hit=turn_hit,
                    turn_mrr=turn_mrr,
                    retrieved_atom_ids=retrieved_atom_ids,
                    latency_ms=elapsed_ms,
                )

                acc = variant_accumulators[variant]
                acc["session_hits"] += 1.0 if session_hit else 0.0
                acc["session_recalls"] += session_recall
                acc["turn_hits"] += 1.0 if turn_hit else 0.0
                acc["turn_mrrs"] += turn_mrr
                acc["latencies"] += elapsed_ms

            evaluated_cases.append(
                CaseAblationResult(
                    question_id=raw_case.question_id,
                    question_type=raw_case.question_type,
                    question=raw_case.question,
                    answer=raw_case.answer,
                    answer_session_ids=imported_case.answer_session_ids,
                    evidence_atom_ids=imported_case.evidence_atom_ids,
                    variants=case_variants,
                )
            )

        case_count = len(evaluated_cases)
        variant_summaries: dict[str, VariantSummaryMetrics] = {}
        for v in variants_to_test:
            acc = variant_accumulators[v]
            count = max(case_count, 1)
            variant_summaries[v] = VariantSummaryMetrics(
                case_count=case_count,
                session_hit_at_k=acc["session_hits"] / count,
                session_recall_at_k=acc["session_recalls"] / count,
                turn_hit_at_k=acc["turn_hits"] / count,
                mean_reciprocal_rank=acc["turn_mrrs"] / count,
                avg_latency_ms=acc["latencies"] / count,
            )

        return GlobalAblationReport(
            dataset_id=import_result.dataset_id,
            dataset_hash=import_result.dataset_hash,
            total_sessions_in_namespace=import_result.session_count,
            total_atoms_in_namespace=import_result.atom_count,
            case_count=case_count,
            top_k=top_k,
            variant_summaries=variant_summaries,
            cases=tuple(evaluated_cases),
        )

    def _execute_retrieval(
        self,
        *,
        query: str,
        namespace: str,
        variant: str,
        top_k: int,
    ) -> Sequence[Any]:
        plan = QueryPlan(
            query=query,
            namespace=namespace,
            top_k=top_k,
            temporal_mode=TemporalMode.NONE,
        )
        result = self.retrieval_service.retrieve(plan)

        if variant == "variant_0_lexical":
            # Sort / filter by lexical score breakdown
            lexical_items = [
                item for item in result.items if item.score.lexical > 0
            ]
            lexical_items.sort(key=lambda item: item.score.lexical, reverse=True)
            return lexical_items[:top_k]

        elif variant == "variant_1_vector":
            # Sort / filter by semantic score breakdown
            semantic_items = [
                item for item in result.items if item.score.semantic > 0
            ]
            semantic_items.sort(key=lambda item: item.score.semantic, reverse=True)
            return semantic_items[:top_k]

        else:
            # Full Core Atom Engine (Ranked by final score)
            return result.items[:top_k]

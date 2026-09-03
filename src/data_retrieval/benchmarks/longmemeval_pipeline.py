from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Any

from data_retrieval.benchmarks.longmemeval import (
    ImportedLongMemEvalCase,
    LongMemEvalIngestService,
)
from data_retrieval.core.identifiers import stable_id
from data_retrieval.retrieval.embedding import Embedder
from data_retrieval.retrieval.models import (
    QueryPlan,
    RetrievalChannels,
    RetrievalItem,
    TemporalMode,
)
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.services.temporal_enrichment import TemporalEnrichmentService
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer
from data_retrieval.tagging.proposals import TagProposer
from data_retrieval.temporal.bridge import TemporalBridge

from .tag_catalog import BenchmarkTagCatalogResolver

ProgressCallback = Callable[[int, int, str], None]


@dataclass(frozen=True, slots=True)
class LongMemEvalPipelineReport:
    dataset_id: str
    dataset_hash: str
    case_count: int
    evaluated_case_count: int
    abstention_case_count: int
    top_k: int
    session_hit_at_k: float
    session_recall_at_k: float
    turn_hit_at_k: float
    turn_recall_at_k: float
    mean_reciprocal_rank: float
    session_mean_reciprocal_rank: float
    turn_mean_reciprocal_rank: float
    direct_session_hit_at_k: float
    direct_session_recall_at_k: float
    direct_turn_hit_at_k: float
    direct_turn_recall_at_k: float
    direct_mean_reciprocal_rank: float
    direct_session_mean_reciprocal_rank: float
    direct_turn_mean_reciprocal_rank: float
    mean_retrieval_latency_ms: float
    category_metrics: dict[str, dict[str, float]]
    cases: tuple[dict[str, Any], ...]

    def as_dict(self, *, include_cases: bool = True) -> dict[str, Any]:
        result: dict[str, Any] = {
            "dataset_id": self.dataset_id,
            "dataset_hash": self.dataset_hash,
            "case_count": self.case_count,
            "evaluated_case_count": self.evaluated_case_count,
            "abstention_case_count": self.abstention_case_count,
            "top_k": self.top_k,
            "session_hit_at_k": self.session_hit_at_k,
            "session_recall_at_k": self.session_recall_at_k,
            "turn_hit_at_k": self.turn_hit_at_k,
            "turn_recall_at_k": self.turn_recall_at_k,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "session_mean_reciprocal_rank": self.session_mean_reciprocal_rank,
            "turn_mean_reciprocal_rank": self.turn_mean_reciprocal_rank,
            "direct_session_hit_at_k": self.direct_session_hit_at_k,
            "direct_session_recall_at_k": self.direct_session_recall_at_k,
            "direct_turn_hit_at_k": self.direct_turn_hit_at_k,
            "direct_turn_recall_at_k": self.direct_turn_recall_at_k,
            "direct_mean_reciprocal_rank": self.direct_mean_reciprocal_rank,
            "direct_session_mean_reciprocal_rank": (
                self.direct_session_mean_reciprocal_rank
            ),
            "direct_turn_mean_reciprocal_rank": self.direct_turn_mean_reciprocal_rank,
            "mean_retrieval_latency_ms": self.mean_retrieval_latency_ms,
            "metric_semantics": {
                "lineage": (
                    "session_hit_at_k, turn_hit_at_k, recalls, and mean_reciprocal_rank "
                    "credit source atoms covered by a retrieved Temporal summary"
                ),
                "direct": (
                    "direct_* metrics credit only exact retrieved raw source atoms, not "
                    "a Temporal summary's broader provenance"
                ),
                "reciprocal_rank": (
                    "legacy mean_reciprocal_rank fields accept either a relevant session "
                    "or answer-bearing turn; use the explicit session_* and turn_* MRR "
                    "fields for controlled comparisons"
                ),
            },
            "category_metrics": self.category_metrics,
        }
        if include_cases:
            result["cases"] = list(self.cases)
        return result


class LongMemEvalPipelineRunner:
    """Run resumable enrichment and evidence retrieval over LongMemEval."""

    def __init__(
        self,
        repository: Repository,
        *,
        tag_proposer: TagProposer | None = None,
        embedder: Embedder | None = None,
        temporal_bridge: TemporalBridge | None = None,
    ) -> None:
        self.repository = repository
        self.tag_proposer = tag_proposer
        self.embedder = embedder
        self.temporal_bridge = temporal_bridge

    def run(
        self,
        *,
        dataset_path: Path,
        dataset_id: str,
        temporal_state_path: Path | None = None,
        namespace_prefix: str = "longmemeval",
        timezone_name: str = "UTC",
        max_cases: int | None = None,
        question_ids: tuple[str, ...] | None = None,
        top_k: int = 10,
        enrich_tags: bool = False,
        enrich_temporal: bool = False,
        enrich_embeddings: bool = False,
        resolve_benchmark_tags: bool = False,
        benchmark_tag_min_confidence: float = 0.65,
        max_workers: int = 1,
        retrieval_channels: RetrievalChannels | None = None,
        query_tags_by_question: Mapping[str, tuple[str, ...]] | None = None,
        query_vectors_by_question: Mapping[str, tuple[float, ...]] | None = None,
        progress: ProgressCallback | None = None,
    ) -> LongMemEvalPipelineReport:
        if top_k <= 0:
            raise ValueError("top_k must be positive")
        if enrich_tags and self.tag_proposer is None:
            raise ValueError("tag enrichment requires a tag proposer")
        if resolve_benchmark_tags and not enrich_tags:
            raise ValueError("benchmark tag resolution requires tag enrichment")
        if enrich_temporal and self.temporal_bridge is None:
            raise ValueError("Temporal enrichment requires a Temporal bridge")
        if enrich_temporal and temporal_state_path is None:
            raise ValueError("Temporal enrichment requires a state path")
        if enrich_embeddings and self.embedder is None:
            raise ValueError("embedding enrichment requires an embedder")
        if max_workers <= 0:
            raise ValueError("max_workers must be positive")

        imported = LongMemEvalIngestService(self.repository).ingest_path(
            path=dataset_path,
            namespace_prefix=namespace_prefix,
            dataset_id=dataset_id,
            timezone_name=timezone_name,
            max_cases=max_cases,
            question_ids=question_ids,
        )
        total = imported.case_count
        results: list[dict[str, Any] | None] = [None] * total

        def process(index: int, case: ImportedLongMemEvalCase) -> tuple[int, dict[str, Any]]:
            if progress:
                progress(index, total, "enriching")
            self._enrich_case(
                case,
                temporal_state_path=self._temporal_state_for_case(
                    temporal_state_path, case, isolate=max_workers > 1
                ),
                timezone_name=timezone_name,
                enrich_tags=enrich_tags,
                enrich_temporal=enrich_temporal,
                enrich_embeddings=enrich_embeddings,
                resolve_benchmark_tags=resolve_benchmark_tags,
                benchmark_tag_min_confidence=benchmark_tag_min_confidence,
            )
            if progress:
                progress(index, total, "retrieving")
            return index - 1, self._evaluate_case(
                case,
                top_k=top_k,
                retrieval_channels=retrieval_channels,
                query_tags=(
                    query_tags_by_question.get(case.question_id, ())
                    if query_tags_by_question is not None
                    else ()
                ),
                query_vector=(
                    query_vectors_by_question.get(case.question_id)
                    if query_vectors_by_question is not None
                    else None
                ),
            )

        if max_workers == 1:
            for index, case in enumerate(imported.cases, start=1):
                result_index, result = process(index, case)
                results[result_index] = result
        else:
            with ThreadPoolExecutor(
                max_workers=max_workers, thread_name_prefix="longmemeval"
            ) as executor:
                futures = {
                    executor.submit(process, index, case): index
                    for index, case in enumerate(imported.cases, start=1)
                }
                for future in as_completed(futures):
                    result_index, result = future.result()
                    results[result_index] = result

        completed_results = [result for result in results if result is not None]
        if len(completed_results) != total:
            raise RuntimeError("LongMemEval pipeline did not produce every case result")

        return self._report(
            dataset_id=imported.dataset_id,
            dataset_hash=imported.dataset_hash,
            top_k=top_k,
            results=completed_results,
        )

    @staticmethod
    def _temporal_state_for_case(
        base_path: Path | None,
        case: ImportedLongMemEvalCase,
        *,
        isolate: bool,
    ) -> Path | None:
        if base_path is None or not isolate:
            return base_path
        suffix = base_path.suffix or ".sqlite3"
        case_key = stable_id("longmemeval-temporal-state", case.question_id)
        return base_path.with_name(f"{base_path.stem}-{case_key}{suffix}")

    def _enrich_case(
        self,
        case: ImportedLongMemEvalCase,
        *,
        temporal_state_path: Path | None,
        timezone_name: str,
        enrich_tags: bool,
        enrich_temporal: bool,
        enrich_embeddings: bool,
        resolve_benchmark_tags: bool,
        benchmark_tag_min_confidence: float,
    ) -> None:
        tag_service = (
            TagEnrichmentService(
                self.repository,
                self.tag_proposer,
                canonicalizer=(
                    SemanticTagCanonicalizer(self.embedder)
                    if self.embedder is not None
                    else None
                ),
            )
            if enrich_tags and self.tag_proposer is not None
            else None
        )
        if tag_service is not None:
            for document_id in case.document_ids:
                tag_service.enrich_document(document_id)

        if enrich_temporal:
            if self.temporal_bridge is None or temporal_state_path is None:
                raise ValueError("Temporal enrichment is not configured")
            temporal_state_path.parent.mkdir(parents=True, exist_ok=True)
            projection = TemporalEnrichmentService(
                self.repository, self.temporal_bridge
            ).enrich_range(
                namespace=case.namespace,
                timeline_id=case.question_id,
                timezone_name=timezone_name,
                range_start=case.history_start,
                range_end=case.history_end + timedelta(minutes=1),
                state_path=temporal_state_path,
            )
            if tag_service is not None:
                tag_service.enrich_document(projection.bundle.document.document_id)

        if resolve_benchmark_tags:
            BenchmarkTagCatalogResolver(
                self.repository,
                minimum_confidence=benchmark_tag_min_confidence,
            ).resolve_namespace(case.namespace)

        if enrich_embeddings:
            if self.embedder is None:
                raise ValueError("embedding enrichment is not configured")
            EmbeddingEnrichmentService(
                self.repository, self.embedder, batch_size=32
            ).enrich_namespace(case.namespace)

    def _evaluate_case(
        self,
        case: ImportedLongMemEvalCase,
        *,
        top_k: int,
        retrieval_channels: RetrievalChannels | None = None,
        query_tags: tuple[str, ...] = (),
        query_vector: tuple[float, ...] | None = None,
    ) -> dict[str, Any]:
        retrieval = RetrievalService(
            self.repository,
            tag_proposer=self.tag_proposer,
            embedder=self.embedder,
            channels=retrieval_channels,
        )
        started = time.perf_counter()
        result = retrieval.retrieve(
            QueryPlan(
                query=case.question,
                namespace=case.namespace,
                query_tags=query_tags,
                query_vector=query_vector,
                top_k=top_k,
                timeline_id=case.question_id,
                temporal_mode=TemporalMode.AUTO,
                reference_time=case.question_date,
            )
        )
        latency_ms = (time.perf_counter() - started) * 1_000
        credited_source_ids = [self._credited_source_ids(item) for item in result.items]
        all_source_ids = tuple(
            sorted({atom_id for source_ids in credited_source_ids for atom_id in source_ids})
        )
        source_atoms = {
            atom.atom_id: atom for atom in self.repository.get_atoms(all_source_ids)
        }
        answer_sessions = set(case.answer_session_ids)
        evidence_atoms = set(case.evidence_atom_ids)
        relevant_ranks: list[int] = []
        session_relevant_ranks: list[int] = []
        turn_relevant_ranks: list[int] = []
        direct_relevant_ranks: list[int] = []
        direct_session_relevant_ranks: list[int] = []
        direct_turn_relevant_ranks: list[int] = []
        found_sessions: set[str] = set()
        found_evidence_atoms: set[str] = set()
        direct_found_sessions: set[str] = set()
        direct_found_evidence_atoms: set[str] = set()
        retrieved_atoms = {
            atom.atom_id: atom
            for atom in self.repository.get_atoms(
                tuple(item.atom_id for item in result.items)
            )
        }
        for rank, (item, source_ids) in enumerate(
            zip(result.items, credited_source_ids, strict=True), start=1
        ):
            matched_atoms = evidence_atoms.intersection(source_ids)
            matched_sessions = {
                str(source_atoms[atom_id].metadata.get("session_id"))
                for atom_id in source_ids
                if atom_id in source_atoms
                and str(source_atoms[atom_id].metadata.get("session_id"))
                in answer_sessions
            }
            if matched_atoms or matched_sessions:
                relevant_ranks.append(rank)
            if matched_sessions:
                session_relevant_ranks.append(rank)
            if matched_atoms:
                turn_relevant_ranks.append(rank)
            found_evidence_atoms.update(matched_atoms)
            found_sessions.update(matched_sessions)

            direct_atom = retrieved_atoms.get(item.atom_id)
            direct_atoms = evidence_atoms.intersection({item.atom_id})
            direct_session_id = (
                str(direct_atom.metadata.get("session_id"))
                if direct_atom is not None
                else ""
            )
            direct_sessions = (
                {direct_session_id} if direct_session_id in answer_sessions else set()
            )
            if direct_atoms or direct_sessions:
                direct_relevant_ranks.append(rank)
            if direct_sessions:
                direct_session_relevant_ranks.append(rank)
            if direct_atoms:
                direct_turn_relevant_ranks.append(rank)
            direct_found_evidence_atoms.update(direct_atoms)
            direct_found_sessions.update(direct_sessions)

        evaluable = not case.question_id.endswith("_abs") and bool(
            answer_sessions or evidence_atoms
        )
        return {
            "question_id": case.question_id,
            "question_type": case.question_type,
            "evaluable": evaluable,
            "session_hit": bool(found_sessions) if evaluable else False,
            "session_recall": (
                len(found_sessions) / len(answer_sessions) if answer_sessions else 0.0
            ),
            "turn_hit": bool(found_evidence_atoms) if evaluable else False,
            "turn_recall": (
                len(found_evidence_atoms) / len(evidence_atoms) if evidence_atoms else 0.0
            ),
            "reciprocal_rank": 1.0 / min(relevant_ranks) if relevant_ranks else 0.0,
            "session_reciprocal_rank": (
                1.0 / min(session_relevant_ranks) if session_relevant_ranks else 0.0
            ),
            "turn_reciprocal_rank": (
                1.0 / min(turn_relevant_ranks) if turn_relevant_ranks else 0.0
            ),
            "direct_session_hit": bool(direct_found_sessions) if evaluable else False,
            "direct_session_recall": (
                len(direct_found_sessions) / len(answer_sessions) if answer_sessions else 0.0
            ),
            "direct_turn_hit": bool(direct_found_evidence_atoms) if evaluable else False,
            "direct_turn_recall": (
                len(direct_found_evidence_atoms) / len(evidence_atoms)
                if evidence_atoms
                else 0.0
            ),
            "direct_reciprocal_rank": (
                1.0 / min(direct_relevant_ranks) if direct_relevant_ranks else 0.0
            ),
            "direct_session_reciprocal_rank": (
                1.0 / min(direct_session_relevant_ranks)
                if direct_session_relevant_ranks
                else 0.0
            ),
            "direct_turn_reciprocal_rank": (
                1.0 / min(direct_turn_relevant_ranks)
                if direct_turn_relevant_ranks
                else 0.0
            ),
            "retrieval_latency_ms": latency_ms,
            "resolved_temporal_mode": result.resolved_temporal_mode.value,
            "low_confidence": result.low_confidence,
            "retrieved_atom_ids": [item.atom_id for item in result.items],
            "retrieved_source_atom_ids": [sorted(ids) for ids in credited_source_ids],
            "retrieval_diagnostics": result.diagnostics,
        }

    @staticmethod
    def _credited_source_ids(item: RetrievalItem) -> set[str]:
        return set(item.lineage_atom_ids) if item.lineage_atom_ids else {item.atom_id}

    @staticmethod
    def _report(
        *,
        dataset_id: str,
        dataset_hash: str,
        top_k: int,
        results: list[dict[str, Any]],
    ) -> LongMemEvalPipelineReport:
        if not results:
            raise ValueError("LongMemEval evaluation produced no cases")
        evaluated = [result for result in results if result["evaluable"]]
        by_category: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for result in evaluated:
            by_category[str(result["question_type"])].append(result)
        category_metrics = {
            category: {
                "case_count": float(len(category_results)),
                "session_hit_at_k": _mean(category_results, "session_hit"),
                "session_recall_at_k": _mean(category_results, "session_recall"),
                "turn_hit_at_k": _mean(category_results, "turn_hit"),
                "turn_recall_at_k": _mean(category_results, "turn_recall"),
                "mean_reciprocal_rank": _mean(category_results, "reciprocal_rank"),
                "session_mean_reciprocal_rank": _mean(
                    category_results, "session_reciprocal_rank"
                ),
                "turn_mean_reciprocal_rank": _mean(
                    category_results, "turn_reciprocal_rank"
                ),
                "direct_session_hit_at_k": _mean(
                    category_results, "direct_session_hit"
                ),
                "direct_session_recall_at_k": _mean(
                    category_results, "direct_session_recall"
                ),
                "direct_turn_hit_at_k": _mean(category_results, "direct_turn_hit"),
                "direct_turn_recall_at_k": _mean(
                    category_results, "direct_turn_recall"
                ),
                "direct_mean_reciprocal_rank": _mean(
                    category_results, "direct_reciprocal_rank"
                ),
                "direct_session_mean_reciprocal_rank": _mean(
                    category_results, "direct_session_reciprocal_rank"
                ),
                "direct_turn_mean_reciprocal_rank": _mean(
                    category_results, "direct_turn_reciprocal_rank"
                ),
            }
            for category, category_results in sorted(by_category.items())
        }
        return LongMemEvalPipelineReport(
            dataset_id=dataset_id,
            dataset_hash=dataset_hash,
            case_count=len(results),
            evaluated_case_count=len(evaluated),
            abstention_case_count=len(results) - len(evaluated),
            top_k=top_k,
            session_hit_at_k=_mean(evaluated, "session_hit"),
            session_recall_at_k=_mean(evaluated, "session_recall"),
            turn_hit_at_k=_mean(evaluated, "turn_hit"),
            turn_recall_at_k=_mean(evaluated, "turn_recall"),
            mean_reciprocal_rank=_mean(evaluated, "reciprocal_rank"),
            session_mean_reciprocal_rank=_mean(
                evaluated, "session_reciprocal_rank"
            ),
            turn_mean_reciprocal_rank=_mean(evaluated, "turn_reciprocal_rank"),
            direct_session_hit_at_k=_mean(evaluated, "direct_session_hit"),
            direct_session_recall_at_k=_mean(evaluated, "direct_session_recall"),
            direct_turn_hit_at_k=_mean(evaluated, "direct_turn_hit"),
            direct_turn_recall_at_k=_mean(evaluated, "direct_turn_recall"),
            direct_mean_reciprocal_rank=_mean(evaluated, "direct_reciprocal_rank"),
            direct_session_mean_reciprocal_rank=_mean(
                evaluated, "direct_session_reciprocal_rank"
            ),
            direct_turn_mean_reciprocal_rank=_mean(
                evaluated, "direct_turn_reciprocal_rank"
            ),
            mean_retrieval_latency_ms=_mean(results, "retrieval_latency_ms"),
            category_metrics=category_metrics,
            cases=tuple(results),
        )


def _mean(results: list[dict[str, Any]], key: str) -> float:
    if not results:
        return 0.0
    return sum(float(result[key]) for result in results) / len(results)

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data_retrieval.domain.models import AtomLink, AtomLinkRelation, IngestionBundle
from data_retrieval.retrieval.embedding import Embedder
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class EvaluationReport:
    case_count: int
    hit_at_k: float
    mean_reciprocal_rank: float
    recall_at_k: float
    forbidden_violation_rate: float
    category_metrics: dict[str, dict[str, float]]
    cases: tuple[dict[str, Any], ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "case_count": self.case_count,
            "hit_at_k": self.hit_at_k,
            "mean_reciprocal_rank": self.mean_reciprocal_rank,
            "recall_at_k": self.recall_at_k,
            "forbidden_violation_rate": self.forbidden_violation_rate,
            "category_metrics": self.category_metrics,
            "cases": list(self.cases),
        }


class EvaluationRunner:
    """Run a small, deterministic retrieval corpus against one configuration."""

    def __init__(self, repository: Repository, *, embedder: Embedder | None = None) -> None:
        self.repository = repository
        self.embedder = embedder

    def run(self, dataset_path: Path) -> EvaluationReport:
        dataset = self._load(dataset_path)
        namespace = str(dataset["namespace"])
        source_atoms = self._ingest_documents(namespace, dataset["documents"])
        if self.embedder is not None:
            EmbeddingEnrichmentService(self.repository, self.embedder).enrich_namespace(namespace)
        retrieval = RetrievalService(self.repository, embedder=self.embedder)
        results: list[dict[str, Any]] = []
        for case in dataset["cases"]:
            expected = {
                atom_id
                for source in case["expected_sources"]
                for atom_id in source_atoms[str(source)]
            }
            forbidden = {
                atom_id
                for source in case.get("forbidden_sources", [])
                for atom_id in source_atoms[str(source)]
            }
            plan = self._plan(namespace, case)
            retrieved = retrieval.retrieve(plan)
            ranked = [item.atom_id for item in retrieved.items]
            relevant_ranks = [
                index + 1 for index, atom_id in enumerate(ranked) if atom_id in expected
            ]
            hits = len(set(ranked).intersection(expected))
            results.append(
                {
                    "id": str(case["id"]),
                    "category": str(case["category"]),
                    "hit": bool(relevant_ranks),
                    "reciprocal_rank": 1.0 / min(relevant_ranks) if relevant_ranks else 0.0,
                    "recall": hits / len(expected),
                    "forbidden_violation": bool(set(ranked).intersection(forbidden)),
                    "resolved_temporal_mode": retrieved.resolved_temporal_mode.value,
                    "retrieved_atom_ids": ranked,
                }
            )
        return self._report(results)

    def _ingest_documents(
        self, namespace: str, documents: list[dict[str, Any]]
    ) -> dict[str, tuple[str, ...]]:
        ingestion = IngestService(self.repository)
        source_atoms: dict[str, tuple[str, ...]] = {}
        pending_supersedes: list[tuple[str, str]] = []
        for document in documents:
            source = str(document["source"])
            occurred_at = self._datetime(document.get("occurred_at"))
            metadata = dict(document.get("metadata", {}))
            result = ingestion.ingest_text(
                namespace=namespace,
                source=source,
                text=str(document["text"]),
                explicit_tags=tuple(str(tag) for tag in document.get("tags", [])),
                occurred_at=occurred_at,
                metadata=metadata,
            )
            source_atoms[source] = result.atom_ids
            if target := document.get("supersedes_source"):
                pending_supersedes.append((source, str(target)))

        for source, target in pending_supersedes:
            source_atom = source_atoms[source][0]
            target_atom = source_atoms[target][0]
            atom = self.repository.get_atom(source_atom)
            if atom is None:
                raise ValueError(f"missing source atom for {source}")
            document = self.repository.get_document(atom.document_id)
            if document is None:
                raise ValueError(f"missing document for {source}")
            self.repository.persist_ingestion(
                IngestionBundle(
                    document=document,
                    atoms=self.repository.get_atoms_for_document(document.document_id),
                    tags=(),
                    atom_tags=(),
                    atom_links=(
                        AtomLink(
                            from_atom_id=source_atom,
                            to_atom_id=target_atom,
                            relation=AtomLinkRelation.SUPERSEDES,
                        ),
                    ),
                )
            )
        return source_atoms

    @staticmethod
    def _plan(namespace: str, case: dict[str, Any]) -> QueryPlan:
        return QueryPlan(
            query=str(case["query"]),
            namespace=namespace,
            query_tags=tuple(str(tag) for tag in case.get("query_tags", [])),
            top_k=int(case.get("top_k", 5)),
            timeline_id=case.get("timeline_id"),
            temporal_mode=TemporalMode(str(case.get("temporal_mode", "none"))),
            as_of=EvaluationRunner._datetime(case.get("as_of")),
            range_start=EvaluationRunner._datetime(case.get("range_start")),
            range_end=EvaluationRunner._datetime(case.get("range_end")),
        )

    @staticmethod
    def _report(results: list[dict[str, Any]]) -> EvaluationReport:
        if not results:
            raise ValueError("evaluation dataset must contain at least one case")
        categories = sorted({str(result["category"]) for result in results})
        category_metrics: dict[str, dict[str, float]] = {}
        for category in categories:
            subset = [result for result in results if result["category"] == category]
            category_metrics[category] = {
                "case_count": float(len(subset)),
                "hit_at_k": EvaluationRunner._mean(subset, "hit"),
                "mean_reciprocal_rank": EvaluationRunner._mean(subset, "reciprocal_rank"),
                "recall_at_k": EvaluationRunner._mean(subset, "recall"),
            }
        return EvaluationReport(
            case_count=len(results),
            hit_at_k=EvaluationRunner._mean(results, "hit"),
            mean_reciprocal_rank=EvaluationRunner._mean(results, "reciprocal_rank"),
            recall_at_k=EvaluationRunner._mean(results, "recall"),
            forbidden_violation_rate=EvaluationRunner._mean(results, "forbidden_violation"),
            category_metrics=category_metrics,
            cases=tuple(results),
        )

    @staticmethod
    def _mean(results: list[dict[str, Any]], key: str) -> float:
        return sum(float(result[key]) for result in results) / len(results)

    @staticmethod
    def _datetime(value: object) -> datetime | None:
        return datetime.fromisoformat(str(value)) if value is not None else None

    @staticmethod
    def _load(path: Path) -> dict[str, Any]:
        parsed = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(parsed, dict):
            raise ValueError("evaluation dataset must be a JSON object")
        if not isinstance(parsed.get("documents"), list) or not isinstance(
            parsed.get("cases"), list
        ):
            raise ValueError("evaluation dataset needs documents and cases arrays")
        return parsed

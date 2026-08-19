from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from data_retrieval.domain.models import (
    AtomKind,
    AtomLink,
    AtomLinkRelation,
    IngestionBundle,
)
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan, TemporalMode
from data_retrieval.retrieval.ollama import OllamaEmbedder
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.services.temporal_enrichment import TemporalEnrichmentService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.ollama import OllamaTagProposer
from data_retrieval.temporal.bridge import TemporalBridge
from data_retrieval.temporal.ollama import OllamaTemporalSummarizer

DEFAULT_EMBEDDING_MODEL = "hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16"


def aware_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"timestamp must include a timezone: {value}")
    return parsed


def progress(message: str) -> None:
    print(f"[project-history-smoke] {message}", file=sys.stderr, flush=True)


def load_fixture(path: Path) -> dict[str, Any]:
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(fixture, dict):
        raise ValueError("fixture root must be a JSON object")
    return fixture


def add_supersedes_link(
    repository: SQLiteRepository,
    *,
    document_id: str,
    newer_atom_id: str,
    older_atom_id: str,
    source: str,
) -> None:
    document = repository.get_document(document_id)
    if document is None:
        raise ValueError(f"missing document after ingestion: {document_id}")
    repository.persist_ingestion(
        IngestionBundle(
            document=document,
            atoms=repository.get_atoms_for_document(document_id),
            tags=(),
            atom_tags=(),
            atom_links=(
                AtomLink(
                    from_atom_id=newer_atom_id,
                    to_atom_id=older_atom_id,
                    relation=AtomLinkRelation.SUPERSEDES,
                    evidence_sources=("project-history-fixture",),
                    metadata={"declared_by_source": source},
                ),
            ),
        )
    )


def run(args: argparse.Namespace) -> tuple[dict[str, Any], bool]:
    fixture_path = args.fixture.resolve()
    database_path = args.database.resolve()
    state_path = args.temporal_state.resolve()
    for name, path in (("database", database_path), ("temporal state", state_path)):
        if path.exists():
            raise FileExistsError(
                f"refusing to overwrite existing {name}: {path}; choose a fresh path"
            )

    fixture = load_fixture(fixture_path)
    namespace = str(fixture["namespace"])
    timeline_id = str(fixture["timeline_id"])
    source_atoms: dict[str, tuple[str, ...]] = {}
    atom_sources: dict[str, str] = {}
    tag_enrichment: list[dict[str, Any]] = []

    embedder = OllamaEmbedder(
        base_url=args.ollama_url,
        model_name=args.embedding_model,
        profile_name=args.embedding_profile,
        timeout_seconds=args.timeout,
    )
    tag_proposer = OllamaTagProposer(
        base_url=args.ollama_url,
        model=args.tag_model,
        timeout_seconds=args.timeout,
    )

    progress(f"creating isolated SQLite database at {database_path}")
    with SQLiteRepository(database_path) as repository:
        ingestion = IngestService(repository)
        tag_service = TagEnrichmentService(repository, tag_proposer)
        supersedes_count = 0

        for event in fixture["events"]:
            source = str(event["source"])
            metadata = dict(event.get("metadata", {}))
            metadata.update({"timeline_id": timeline_id, "fixture_source": source})
            supersedes_source = event.get("supersedes_source")
            if supersedes_source:
                older_ids = source_atoms.get(str(supersedes_source))
                if not older_ids:
                    raise ValueError(
                        f"{source} supersedes unknown or later source {supersedes_source}"
                    )
                metadata["supersedes_event_id"] = older_ids[0]

            result = ingestion.ingest_text(
                namespace=namespace,
                source=source,
                text=str(event["text"]),
                explicit_tags=tuple(str(tag) for tag in event.get("tags", ())),
                occurred_at=aware_datetime(str(event["occurred_at"])),
                metadata=metadata,
            )
            source_atoms[source] = result.atom_ids
            atom_sources.update({atom_id: source for atom_id in result.atom_ids})

            if supersedes_source:
                add_supersedes_link(
                    repository,
                    document_id=result.document_id,
                    newer_atom_id=result.atom_ids[0],
                    older_atom_id=source_atoms[str(supersedes_source)][0],
                    source=source,
                )
                supersedes_count += 1

            if event.get("ai_tag_enrichment"):
                progress(f"asking {args.tag_model} to enrich tags for {source}")
                enriched = tag_service.enrich_document(result.document_id)
                tag_enrichment.append(
                    {
                        "source": source,
                        "tag_count": len(enriched.tag_ids),
                        "atom_tag_count": enriched.atom_tag_count,
                    }
                )

        progress(f"embedding {len(atom_sources)} source atoms with {args.embedding_model}")
        before_projection = EmbeddingEnrichmentService(repository, embedder).enrich_namespace(
            namespace
        )

        progress(f"building live temporal summaries with {args.temporal_model}")
        projection = TemporalEnrichmentService(
            repository,
            TemporalBridge(
                OllamaTemporalSummarizer(
                    base_url=args.ollama_url,
                    model=args.temporal_model,
                    timeout_seconds=args.timeout,
                )
            ),
        ).enrich_range(
            namespace=namespace,
            timeline_id=timeline_id,
            timezone_name=str(fixture["timezone"]),
            range_start=aware_datetime(str(fixture["range_start"])),
            range_end=aware_datetime(str(fixture["range_end"])),
            state_path=state_path,
            max_workers=args.temporal_workers,
        )

        progress("embedding the generated temporal summary atoms")
        after_projection = EmbeddingEnrichmentService(repository, embedder).enrich_namespace(
            namespace
        )
        retrieval = RetrievalService(repository, embedder=embedder)

        query_results: list[dict[str, Any]] = []
        for case in fixture["queries"]:
            plan = QueryPlan(
                query=str(case["query"]),
                namespace=namespace,
                query_tags=tuple(str(tag) for tag in case.get("query_tags", ())),
                top_k=int(case.get("top_k", 5)),
                timeline_id=timeline_id,
                temporal_mode=TemporalMode(str(case["temporal_mode"])),
                as_of=(aware_datetime(str(case["as_of"])) if case.get("as_of") else None),
            )
            result = retrieval.retrieve(plan)
            returned_sources = tuple(
                dict.fromkeys(
                    atom_sources[item.atom_id]
                    for item in result.items
                    if item.atom_id in atom_sources
                )
            )
            returned_kinds = {item.kind.value for item in result.items}
            expected = set(str(source) for source in case.get("expected_sources", ()))
            forbidden = set(str(source) for source in case.get("forbidden_sources", ()))
            missing = sorted(expected - set(returned_sources))
            forbidden_hits = sorted(forbidden & set(returned_sources))
            required_kind = case.get("require_kind")
            kind_present = required_kind is None or str(required_kind) in returned_kinds
            passed = not missing and not forbidden_hits and kind_present
            query_results.append(
                {
                    "id": str(case["id"]),
                    "passed": passed,
                    "resolved_temporal_mode": result.resolved_temporal_mode.value,
                    "missing_expected_sources": missing,
                    "forbidden_source_hits": forbidden_hits,
                    "required_kind_present": kind_present,
                    "items": [
                        {
                            "source": atom_sources.get(item.atom_id),
                            "kind": item.kind.value,
                            "role": item.role,
                            "score": round(item.score.final, 4),
                            "content": item.content,
                        }
                        for item in result.items
                    ],
                }
            )
            progress(f"query {case['id']}: {'PASS' if passed else 'FAIL'}")

        summaries = repository.list_atoms(namespace=namespace, kind=AtomKind.TEMPORAL_SUMMARY)
        links = repository.list_atom_links(namespace=namespace)
        summarizes_links = tuple(
            link for link in links if link.relation is AtomLinkRelation.SUMMARIZES
        )
        derived_links = tuple(
            link for link in links if link.relation is AtomLinkRelation.DERIVED_FROM
        )
        summary_ids = {atom.atom_id for atom in summaries}
        source_lineage_count = sum(
            link.from_atom_id in summary_ids and link.to_atom_id in atom_sources
            for link in summarizes_links
        )
        lineage_passed = bool(summaries) and source_lineage_count > 0

        progress("checking explicit-outcome learning on two returned source atoms")
        feedback_retrieval = retrieval.retrieve(
            QueryPlan(
                query="weighted retrieval pipeline with explicit outcomes",
                namespace=namespace,
                query_tags=("weighted retrieval", "retrieval pipeline"),
                top_k=10,
                timeline_id=timeline_id,
                temporal_mode=TemporalMode.NONE,
            )
        )
        desired_feedback_sources = (
            "history/original-tag-logic",
            "history/staged-retrieval",
        )
        returned_ids = {item.atom_id for item in feedback_retrieval.items}
        selected_ids = tuple(
            source_atoms[source][0]
            for source in desired_feedback_sources
            if source_atoms[source][0] in returned_ids
        )
        if len(selected_ids) < 2:
            selected_ids = tuple(
                item.atom_id
                for item in feedback_retrieval.items
                if item.kind is AtomKind.SOURCE
            )[:2]
        if len(selected_ids) < 2:
            raise RuntimeError("feedback retrieval did not return two source atoms")
        feedback = LearningService(repository).apply_feedback(
            FeedbackRequest(
                feedback_id="project-history-smoke-feedback-v1",
                retrieval_id=feedback_retrieval.retrieval_id,
                selected_atom_ids=selected_ids,
                outcome="positive",
                reason="acceptance test for explicit outcome learning",
            )
        )
        learned_links = repository.list_atom_links(
            namespace=namespace, relation=AtomLinkRelation.CO_USED
        )
        feedback_passed = feedback.atom_link_updates > 0 and bool(learned_links)

        query_pass_count = sum(bool(result["passed"]) for result in query_results)
        report = {
            "fixture": str(fixture_path),
            "database": str(database_path),
            "temporal_state": str(state_path),
            "namespace": namespace,
            "models": {
                "tag": args.tag_model,
                "temporal": args.temporal_model,
                "embedding": embedder.model,
            },
            "ingestion": {
                "event_count": len(fixture["events"]),
                "source_atom_count": len(atom_sources),
                "explicit_supersedes_links": supersedes_count,
                "ai_tag_enrichments": tag_enrichment,
            },
            "embeddings": {
                "source_atoms_embedded": len(before_projection.embedded_atom_ids),
                "summary_atoms_embedded": len(after_projection.embedded_atom_ids),
                "source_atoms_reused_after_projection": len(after_projection.reused_atom_ids),
            },
            "temporal_projection": {
                "summary_counts": projection.summary_counts,
                "coverage_count": projection.coverage_count,
                "persisted_summary_atoms": len(summaries),
                "summarizes_links": len(summarizes_links),
                "derived_from_links": len(derived_links),
                "direct_source_lineage_links": source_lineage_count,
                "passed": lineage_passed,
            },
            "retrieval": {
                "passed": query_pass_count,
                "total": len(query_results),
                "queries": query_results,
            },
            "feedback_learning": {
                "selected_sources": [atom_sources.get(atom_id) for atom_id in selected_ids],
                "credited_atom_count": len(feedback.credited_atom_ids),
                "atom_tag_updates": feedback.atom_tag_updates,
                "atom_link_updates": feedback.atom_link_updates,
                "tag_relation_updates": feedback.tag_relation_updates,
                "co_used_link_count": len(learned_links),
                "passed": feedback_passed,
            },
        }
        passed = (
            query_pass_count == len(query_results) and lineage_passed and feedback_passed
        )
        report["passed"] = passed
        return report, passed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="End-to-end acceptance test for tag retrieval plus temporal history."
    )
    parser.add_argument(
        "--fixture", type=Path, default=Path("evals/project_history_smoke.json")
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--temporal-state", type=Path, required=True)
    parser.add_argument(
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    )
    parser.add_argument("--tag-model", default="gemma4:12b-mlx")
    parser.add_argument("--temporal-model", default="gemma4:12b-mlx")
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--embedding-profile", default="harrier-retrieval-v1")
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument("--temporal-workers", type=int, default=1)
    args = parser.parse_args()

    try:
        report, passed = run(args)
    except Exception as error:  # noqa: BLE001 - this is a CLI test boundary
        print(
            json.dumps(
                {"passed": False, "error_type": type(error).__name__, "error": str(error)},
                ensure_ascii=False,
                indent=2,
            )
        )
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())

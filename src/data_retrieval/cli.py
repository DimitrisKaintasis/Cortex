from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from data_retrieval.evaluation import EvaluationRunner
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan, TemporalMode
from data_retrieval.retrieval.ollama import EMBEDDING_PROFILES, OllamaEmbedder
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.services.temporal_enrichment import TemporalEnrichmentService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.ollama import OllamaError, OllamaTagProposer
from data_retrieval.temporal import TemporalBridge
from data_retrieval.temporal.ollama import OllamaTemporalSummarizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="data-retrieval")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="ingest one UTF-8 text file")
    ingest.add_argument("path", type=Path)
    ingest.add_argument("--db", type=Path, default=_env_path("DATA_RETRIEVAL_DB", "data.sqlite3"))
    ingest.add_argument("--namespace", required=True)
    ingest.add_argument("--source", help="stable source label; defaults to the input path")
    ingest.add_argument("--tag", action="append", default=[], dest="tags")
    ingest.add_argument("--occurred-at", type=_aware_datetime)
    ingest.add_argument("--timeline-id")
    ingest.add_argument(
        "--metadata-json",
        type=_json_object,
        default={},
        help="additional source metadata as one JSON object",
    )

    tags = commands.add_parser(
        "enrich-tags", help="add Ollama tag proposals to an ingested document"
    )
    tags.add_argument("document_id")
    tags.add_argument("--db", type=Path, default=_env_path("DATA_RETRIEVAL_DB", "data.sqlite3"))
    _add_ollama_options(tags, timeout_default="120")

    temporal = commands.add_parser(
        "enrich-temporal", help="create Temporal History summary atoms from stored atoms"
    )
    temporal.add_argument("--db", type=Path, default=_env_path("DATA_RETRIEVAL_DB", "data.sqlite3"))
    temporal.add_argument("--namespace", required=True)
    temporal.add_argument("--timeline-id", required=True)
    temporal.add_argument("--range-start", required=True, type=_aware_datetime)
    temporal.add_argument("--range-end", required=True, type=_aware_datetime)
    temporal.add_argument("--timezone", default="UTC", dest="timezone_name")
    temporal.add_argument("--state", type=Path)
    temporal.add_argument("--max-workers", type=int, default=1)
    _add_ollama_options(temporal, timeout_default="240")

    embeddings = commands.add_parser(
        "enrich-embeddings", help="embed all changed atoms in one namespace"
    )
    embeddings.add_argument(
        "--db", type=Path, default=_env_path("DATA_RETRIEVAL_DB", "data.sqlite3")
    )
    embeddings.add_argument("--namespace", required=True)
    _add_embedding_options(embeddings)

    retrieve = commands.add_parser("retrieve", help="run explainable hybrid retrieval")
    retrieve.add_argument("query")
    retrieve.add_argument("--db", type=Path, default=_env_path("DATA_RETRIEVAL_DB", "data.sqlite3"))
    retrieve.add_argument("--namespace", required=True)
    retrieve.add_argument("--tag", action="append", default=[], dest="tags")
    retrieve.add_argument("--top-k", type=int, default=10)
    retrieve.add_argument("--timeline-id")
    retrieve.add_argument(
        "--temporal-mode",
        choices=tuple(mode.value for mode in TemporalMode),
        default=TemporalMode.AUTO.value,
    )
    retrieve.add_argument("--as-of", type=_aware_datetime)
    retrieve.add_argument("--range-start", type=_aware_datetime)
    retrieve.add_argument("--range-end", type=_aware_datetime)
    _add_embedding_options(retrieve, required=False)

    feedback = commands.add_parser(
        "feedback", help="apply one explicit outcome to a recorded retrieval"
    )
    feedback.add_argument("retrieval_id")
    feedback.add_argument("--db", type=Path, default=_env_path("DATA_RETRIEVAL_DB", "data.sqlite3"))
    feedback.add_argument("--feedback-id", default=None)
    feedback.add_argument("--selected-atom", action="append", required=True)
    feedback.add_argument("--outcome", choices=("positive", "negative"), required=True)
    feedback.add_argument("--reason", default="")

    evaluate = commands.add_parser("evaluate", help="run the retrieval evaluation corpus")
    evaluate.add_argument("--dataset", type=Path, default=Path("evals/retrieval_cases.json"))
    evaluate.add_argument(
        "--db", type=Path, default=_env_path("DATA_RETRIEVAL_EVAL_DB", "evaluation.sqlite3")
    )
    _add_embedding_options(evaluate, required=False)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"enrich-tags", "enrich-temporal"} and not args.ollama_model:
        parser.error("--ollama-model or OLLAMA_MODEL is required")
    if args.command == "enrich-embeddings" and not args.embedding_model:
        parser.error("--embedding-model or OLLAMA_EMBEDDING_MODEL is required")

    try:
        if args.command == "ingest":
            output = _ingest(args, parser)
        elif args.command == "enrich-tags":
            output = _enrich_tags(args)
        elif args.command == "enrich-temporal":
            output = _enrich_temporal(args)
        elif args.command == "enrich-embeddings":
            output = _enrich_embeddings(args)
        elif args.command == "retrieve":
            output = _retrieve(args)
        elif args.command == "feedback":
            output = _feedback(args)
        else:
            output = _evaluate(args)
    except (OSError, UnicodeError, OllamaError, ValueError, sqlite3.Error) as error:
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(output, indent=2))
    return 0


def _ingest(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, object]:
    if not args.path.is_file():
        parser.error(f"input file does not exist: {args.path}")
    text = args.path.read_text(encoding="utf-8")
    metadata = {"source_type": "text_file", **args.metadata_json}
    if args.timeline_id:
        metadata["timeline_id"] = args.timeline_id
    with SQLiteRepository(args.db) as repository:
        result = IngestService(repository).ingest_text(
            namespace=args.namespace,
            source=args.source or args.path.as_posix(),
            text=text,
            explicit_tags=tuple(args.tags),
            occurred_at=args.occurred_at,
            metadata=metadata,
        )
    return {
        "document_id": result.document_id,
        "atom_ids": result.atom_ids,
        "tag_ids": result.tag_ids,
        "idempotent": result.idempotent,
        "database": str(args.db),
    }


def _enrich_tags(args: argparse.Namespace) -> dict[str, object]:
    proposer = OllamaTagProposer(
        base_url=args.ollama_url,
        model=args.ollama_model,
        timeout_seconds=args.ollama_timeout,
    )
    with SQLiteRepository(args.db) as repository:
        result = TagEnrichmentService(repository, proposer).enrich_document(args.document_id)
    return {
        "document_id": result.document_id,
        "tag_ids": result.tag_ids,
        "atom_tag_count": result.atom_tag_count,
        "idempotent": result.idempotent,
        "database": str(args.db),
    }


def _enrich_temporal(args: argparse.Namespace) -> dict[str, object]:
    state_path = args.state or args.db.with_name(f"{args.db.stem}.temporal-state.sqlite3")
    summarizer = OllamaTemporalSummarizer(
        base_url=args.ollama_url,
        model=args.ollama_model,
        timeout_seconds=args.ollama_timeout,
    )
    with SQLiteRepository(args.db) as repository:
        result = TemporalEnrichmentService(repository, TemporalBridge(summarizer)).enrich_range(
            namespace=args.namespace,
            timeline_id=args.timeline_id,
            timezone_name=args.timezone_name,
            range_start=args.range_start,
            range_end=args.range_end,
            state_path=state_path,
            max_workers=args.max_workers,
        )
    return {
        "document_id": result.bundle.document.document_id,
        "summary_counts": result.summary_counts,
        "coverage_count": result.coverage_count,
        "generation": result.generation,
        "database": str(args.db),
        "temporal_state": str(state_path),
    }


def _enrich_embeddings(args: argparse.Namespace) -> dict[str, object]:
    embedder = _embedder(args)
    with SQLiteRepository(args.db) as repository:
        result = EmbeddingEnrichmentService(repository, embedder).enrich_namespace(args.namespace)
    return {
        "namespace": result.namespace,
        "embedded_atom_ids": result.embedded_atom_ids,
        "reused_atom_ids": result.reused_atom_ids,
        "provider": embedder.provider,
        "model": embedder.model,
        "database": str(args.db),
    }


def _retrieve(args: argparse.Namespace) -> dict[str, object]:
    embedder = _embedder(args) if args.embedding_model else None
    plan = QueryPlan(
        query=args.query,
        namespace=args.namespace,
        query_tags=tuple(args.tags),
        top_k=args.top_k,
        timeline_id=args.timeline_id,
        temporal_mode=TemporalMode(args.temporal_mode),
        as_of=args.as_of,
        range_start=args.range_start,
        range_end=args.range_end,
    )
    with SQLiteRepository(args.db) as repository:
        result = RetrievalService(repository, embedder=embedder).retrieve(plan)
    return {
        "retrieval_id": result.retrieval_id,
        "resolved_temporal_mode": result.resolved_temporal_mode.value,
        "low_confidence": result.low_confidence,
        "diagnostics": result.diagnostics,
        "items": [
            {
                "atom_id": item.atom_id,
                "content": item.content,
                "kind": item.kind.value,
                "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
                "role": item.role,
                "lineage_atom_ids": item.lineage_atom_ids,
                "score": {
                    "tag": item.score.tag,
                    "lexical": item.score.lexical,
                    "semantic": item.score.semantic,
                    "relationship": item.score.relationship,
                    "temporal": item.score.temporal,
                    "final": item.score.final,
                    "evidence": item.score.evidence,
                },
            }
            for item in result.items
        ],
    }


def _feedback(args: argparse.Namespace) -> dict[str, object]:
    feedback_id = args.feedback_id or str(uuid4())
    with SQLiteRepository(args.db) as repository:
        result = LearningService(repository).apply_feedback(
            FeedbackRequest(
                feedback_id=feedback_id,
                retrieval_id=args.retrieval_id,
                selected_atom_ids=tuple(args.selected_atom),
                outcome=args.outcome,
                reason=args.reason,
            )
        )
    return {
        "feedback_id": result.feedback_id,
        "credited_atom_ids": result.credited_atom_ids,
        "atom_tag_updates": result.atom_tag_updates,
        "atom_link_updates": result.atom_link_updates,
        "tag_relation_updates": result.tag_relation_updates,
        "database": str(args.db),
    }


def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    embedder = _embedder(args) if args.embedding_model else None
    with SQLiteRepository(args.db) as repository:
        report = EvaluationRunner(repository, embedder=embedder).run(args.dataset)
    return {
        **report.as_dict(),
        "dataset": str(args.dataset),
        "database": str(args.db),
        "embedding_model": embedder.model if embedder else None,
    }


def _add_ollama_options(parser: argparse.ArgumentParser, *, timeout_default: str) -> None:
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435"),
    )
    parser.add_argument("--ollama-model", default=os.getenv("OLLAMA_MODEL"))
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", timeout_default)),
    )


def _add_embedding_options(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435"),
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("OLLAMA_EMBEDDING_MODEL"),
        required=required and not os.getenv("OLLAMA_EMBEDDING_MODEL"),
    )
    parser.add_argument(
        "--embedding-profile",
        choices=tuple(EMBEDDING_PROFILES),
        default=os.getenv("OLLAMA_EMBEDDING_PROFILE", "symmetric"),
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")),
    )


def _embedder(args: argparse.Namespace) -> OllamaEmbedder:
    return OllamaEmbedder(
        base_url=args.ollama_url,
        model_name=args.embedding_model,
        timeout_seconds=args.ollama_timeout,
        profile_name=args.embedding_profile,
    )


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a UTC offset")
    return parsed


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("expected a JSON object") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("metadata must be a JSON object")
    return parsed


if __name__ == "__main__":
    raise SystemExit(main())

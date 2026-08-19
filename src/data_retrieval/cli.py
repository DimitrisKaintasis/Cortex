from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path

from data_retrieval.services.ingestion import IngestService
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"enrich-tags", "enrich-temporal"} and not args.ollama_model:
        parser.error("--ollama-model or OLLAMA_MODEL is required")

    try:
        if args.command == "ingest":
            output = _ingest(args, parser)
        elif args.command == "enrich-tags":
            output = _enrich_tags(args)
        else:
            output = _enrich_temporal(args)
    except (OSError, UnicodeError, OllamaError, ValueError) as error:
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

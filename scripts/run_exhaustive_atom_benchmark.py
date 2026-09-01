from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Add src to sys.path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_retrieval.benchmarks.global_ablation import GlobalAblationRunner
from data_retrieval.retrieval.ollama import EMBEDDING_PROFILES, OllamaEmbedder
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.postgresql import PostgreSQLRepository
from data_retrieval.storage.sqlite import SQLiteRepository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run exhaustive, unconfounded atom architecture benchmarks in a single global namespace."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/benchmarks/longmemeval/longmemeval_oracle.json"),
        help="Path to LongMemEval JSON dataset file",
    )
    parser.add_argument(
        "--dataset-id",
        default="longmemeval-oracle-global",
        help="Dataset identifier",
    )
    parser.add_argument(
        "--max-cases",
        type=int,
        default=50,
        help="Maximum number of evaluation cases to benchmark",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=10,
        help="Top-K candidates to retrieve per query",
    )
    parser.add_argument(
        "--database",
        type=Path,
        default=Path("data/benchmark_global.sqlite3"),
        help="SQLite database path (ignored if --in-memory or --postgres-dsn is set)",
    )
    parser.add_argument(
        "--in-memory",
        action="store_true",
        help="Use ephemeral in-memory SQLite storage",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=os.getenv("DATA_RETRIEVAL_POSTGRES_DSN"),
        help="PostgreSQL connection string",
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("OLLAMA_EMBEDDING_MODEL"),
        help="Ollama embedding model (optional)",
    )
    parser.add_argument(
        "--embedding-profile",
        choices=tuple(EMBEDDING_PROFILES),
        default=os.getenv("OLLAMA_EMBEDDING_PROFILE", "symmetric"),
    )
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435"),
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=Path("data/results/exhaustive_atom_ablation_report.json"),
        help="Path to save the JSON benchmark report",
    )

    args = parser.parse_args()

    if not args.dataset.is_file():
        print(f"Error: Dataset file not found at {args.dataset}", file=sys.stderr)
        return 1

    embedder = None
    if args.embedding_model:
        embedder = OllamaEmbedder(
            base_url=args.ollama_url,
            model=args.embedding_model,
            profile=args.embedding_profile,
        )

    print(f"=== Starting Exhaustive Atom Architecture Benchmark ===", file=sys.stderr)
    print(f"Dataset: {args.dataset}", file=sys.stderr)
    print(f"Max cases: {args.max_cases} (Top-K: {args.top_k})", file=sys.stderr)
    print(f"Mode: Single Shared Global Namespace (Unconfounded)", file=sys.stderr)

    if args.postgres_dsn:
        repository = PostgreSQLRepository(args.postgres_dsn)
        repository.initialize_schema()
        print("Storage: PostgreSQL + pgvector", file=sys.stderr)
    elif args.in_memory:
        repository = InMemoryRepository()
        print("Storage: In-Memory SQLite", file=sys.stderr)
    else:
        args.database.parent.mkdir(parents=True, exist_ok=True)
        repository = SQLiteRepository(args.database)
        print(f"Storage: SQLite file ({args.database})", file=sys.stderr)

    runner = GlobalAblationRunner(repository, embedder=embedder)
    report = runner.run(
        dataset_path=args.dataset,
        dataset_id=args.dataset_id,
        max_cases=args.max_cases,
        top_k=args.top_k,
    )

    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    report_data = report.as_dict()

    args.output_report.write_text(
        json.dumps(report_data, indent=2), encoding="utf-8"
    )

    print("\n=== Benchmark Execution Complete ===", file=sys.stderr)
    print(f"Total sessions in pool: {report.total_sessions_in_namespace}", file=sys.stderr)
    print(f"Total atoms in pool: {report.total_atoms_in_namespace}", file=sys.stderr)
    print(f"Evaluated cases: {report.case_count}\n", file=sys.stderr)

    print(json.dumps(report_data["variant_summaries"], indent=2))
    print(f"\nFull report written to: {args.output_report}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

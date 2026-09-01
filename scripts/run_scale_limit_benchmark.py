from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_retrieval.benchmarks.longmemeval import LongMemEvalIngestService, iter_longmemeval_cases
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.postgresql import PostgreSQLRepository
from data_retrieval.storage.sqlite import SQLiteRepository


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Dataset Scale Limit Benchmark comparing Atom Graph retrieval at and past Mem0 limits."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/benchmarks/longmemeval/longmemeval_s_cleaned.json"),
        help="Path to LongMemEval Cleaned-S JSON dataset file (277 MB)",
    )
    parser.add_argument(
        "--dataset-id",
        default="cleaned-s-scale",
        help="Dataset identifier",
    )
    parser.add_argument(
        "--cases-list",
        nargs="+",
        type=int,
        default=[20, 50, 100],
        help="List of max-cases milestones to evaluate scale curve (e.g. 20 50 100 500)",
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
        default=Path("data/scale_limit_benchmark.sqlite3"),
        help="SQLite database path",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=os.getenv("DATA_RETRIEVAL_POSTGRES_DSN"),
        help="PostgreSQL DSN for scale testing",
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=Path("data/results/dataset_scale_limit_report.json"),
        help="Path to save scale report JSON",
    )

    args = parser.parse_args()

    if not args.dataset.is_file():
        print(f"Error: Dataset file not found at {args.dataset}", file=sys.stderr)
        return 1

    print("============================================================", file=sys.stderr)
    print("=== STARTING DATASET SCALE LIMIT BENCHMARK (PAST MEM0 LIMITS) ===", file=sys.stderr)
    print(f"Dataset: {args.dataset}", file=sys.stderr)
    print(f"Scale Milestones (Cases): {args.cases_list}", file=sys.stderr)
    print("============================================================", file=sys.stderr)

    tier_results = []

    for max_cases in sorted(args.cases_list):
        print(f"\n--- Testing Scale Milestone: {max_cases} Cases ---", file=sys.stderr)
        
        # Fresh db for isolated tier scale measurement
        if args.postgres_dsn:
            repository = PostgreSQLRepository(args.postgres_dsn)
            repository.initialize_schema()
            db_label = "PostgreSQL"
        else:
            tier_db_path = args.database.parent / f"{args.database.stem}_{max_cases}.sqlite3"
            tier_db_path.parent.mkdir(parents=True, exist_ok=True)
            if tier_db_path.exists():
                tier_db_path.unlink()
            repository = SQLiteRepository(tier_db_path)
            db_label = f"SQLite ({tier_db_path.name})"

        ingest_service = LongMemEvalIngestService(repository)
        start_ingest = time.perf_counter()
        import_res = ingest_service.ingest_path(
            path=args.dataset,
            dataset_id=f"{args.dataset_id}-{max_cases}",
            max_cases=max_cases,
            use_global_namespace=True,
        )
        ingest_time_sec = time.perf_counter() - start_ingest
        global_namespace = import_res.cases[0].namespace

        print(f"Ingested {import_res.session_count} sessions ({import_res.atom_count} turn atoms) in {ingest_time_sec:.2f}s", file=sys.stderr)

        # Run retrieval evaluations across all ingested cases
        retrieval_service = RetrievalService(repository)
        case_map = {c.question_id: c for c in import_res.cases}
        raw_cases = list(iter_longmemeval_cases(args.dataset))[:max_cases]

        hits = 0
        mrr_sum = 0.0
        total_latency_ms = 0.0
        eval_count = 0

        for case in raw_cases:
            if case.question_id not in case_map:
                continue

            imported_case = case_map[case.question_id]
            target_sessions = set(imported_case.answer_session_ids)
            target_turns = set(imported_case.evidence_atom_ids)

            plan = QueryPlan(
                query=case.question,
                namespace=global_namespace,
                top_k=args.top_k,
                temporal_mode=TemporalMode.NONE,
            )

            start_query = time.perf_counter()
            retrieval_res = retrieval_service.retrieve(plan)
            latency_ms = (time.perf_counter() - start_query) * 1000.0
            total_latency_ms += latency_ms

            hit = False
            rank_found = 0
            for rank, item in enumerate(retrieval_res.items, start=1):
                if item.atom_id in target_turns or item.metadata.get("session_id") in target_sessions:
                    hit = True
                    rank_found = rank
                    break

            if hit:
                hits += 1
                mrr_sum += 1.0 / rank_found

            eval_count += 1

        tier_summary = {
            "max_cases": max_cases,
            "total_sessions_in_global_pool": import_res.session_count,
            "total_atoms_in_global_pool": import_res.atom_count,
            "ingest_time_seconds": round(ingest_time_sec, 2),
            "evaluated_cases": eval_count,
            "session_hit_at_10": round(hits / eval_count, 4) if eval_count else 0.0,
            "mean_reciprocal_rank": round(mrr_sum / eval_count, 4) if eval_count else 0.0,
            "avg_latency_ms": round(total_latency_ms / eval_count, 2) if eval_count else 0.0,
            "database": db_label,
        }

        tier_results.append(tier_summary)
        print(f"Milestone {max_cases} Cases -> Pool: {import_res.session_count} sessions ({import_res.atom_count} atoms) | Hit@10: {tier_summary['session_hit_at_10'] * 100:.1f}% | MRR: {tier_summary['mean_reciprocal_rank']:.3f} | Latency: {tier_summary['avg_latency_ms']} ms", file=sys.stderr)

    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    report_data = {
        "dataset": str(args.dataset),
        "scale_curve": tier_results,
    }
    args.output_report.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

    print("\n============================================================", file=sys.stderr)
    print(f"=== SCALE LIMIT BENCHMARK COMPLETE ===", file=sys.stderr)
    print(json.dumps(tier_results, indent=2))
    print(f"Full scale report saved to: {args.output_report}", file=sys.stderr)
    print("============================================================", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())

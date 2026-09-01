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
        description="Run Massive Scale Benchmark on 264MB Cleaned-S Dataset (up to 246,750 turns)."
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=Path("data/benchmarks/longmemeval/longmemeval_s_cleaned.json"),
        help="Path to LongMemEval Cleaned-S dataset",
    )
    parser.add_argument(
        "--milestones",
        nargs="+",
        type=int,
        default=[100, 250, 500],
        help="Cases milestones to test (e.g. 100 250 500)",
    )
    parser.add_argument(
        "--database-dir",
        type=Path,
        default=Path("data/massive_scale"),
        help="Directory to store benchmark SQLite databases",
    )
    parser.add_argument(
        "--output-report",
        type=Path,
        default=Path("data/results/massive_scale_benchmark_report.json"),
        help="Path to output final JSON scale report",
    )

    args = parser.parse_args()

    if not args.dataset.is_file():
        print(f"Error: Dataset file not found at {args.dataset}", file=sys.stderr)
        return 1

    print("============================================================", file=sys.stderr)
    print("=== STARTING MASSIVE SCALE BENCHMARK (264MB / 246,750 TURNS) ===", file=sys.stderr)
    print(f"Dataset: {args.dataset}", file=sys.stderr)
    print(f"Scale Milestones: {args.milestones} Cases", file=sys.stderr)
    print("============================================================", file=sys.stderr)

    scale_curve = []

    for max_cases in sorted(args.milestones):
        print(f"\n============================================================", file=sys.stderr)
        print(f"=== MILESTONE TIER: {max_cases} Cases ===", file=sys.stderr)
        print(f"============================================================", file=sys.stderr)

        args.database_dir.mkdir(parents=True, exist_ok=True)
        db_path = args.database_dir / f"massive_scale_{max_cases}.sqlite3"
        if db_path.exists():
            db_path.unlink()

        repository = SQLiteRepository(db_path)
        ingest_service = LongMemEvalIngestService(repository)

        start_ingest = time.perf_counter()
        import_res = ingest_service.ingest_path(
            path=args.dataset,
            dataset_id=f"cleaned-s-global-{max_cases}",
            max_cases=max_cases,
            use_global_namespace=True,
        )
        ingest_time_sec = time.perf_counter() - start_ingest
        global_namespace = import_res.cases[0].namespace

        print(f"Ingested {import_res.session_count} sessions ({import_res.atom_count} atoms) into global namespace '{global_namespace}' in {ingest_time_sec:.2f}s", file=sys.stderr)

        # Execute multi-channel hybrid retrieval across all cases in tier
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
                top_k=10,
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

        tier_info = {
            "milestone_cases": max_cases,
            "total_sessions_in_global_pool": import_res.session_count,
            "total_atoms_in_global_pool": import_res.atom_count,
            "ingest_time_seconds": round(ingest_time_sec, 2),
            "evaluated_cases": eval_count,
            "session_hit_at_10": round(hits / eval_count, 4) if eval_count else 0.0,
            "mean_reciprocal_rank": round(mrr_sum / eval_count, 4) if eval_count else 0.0,
            "avg_query_latency_ms": round(total_latency_ms / eval_count, 2) if eval_count else 0.0,
        }

        scale_curve.append(tier_info)
        print(f"--> Tier {max_cases} Cases Result: Pool of {import_res.session_count} sessions ({import_res.atom_count} atoms) | Hit@10: {tier_info['session_hit_at_10'] * 100:.1f}% | MRR: {tier_info['mean_reciprocal_rank']:.3f} | Latency: {tier_info['avg_query_latency_ms']} ms", file=sys.stderr)

    report_data = {
        "dataset_name": "LongMemEval Cleaned-S (264.53 MB)",
        "total_cases_in_dataset": 500,
        "total_sessions_in_dataset": 23867,
        "scale_curve": scale_curve,
    }

    args.output_report.parent.mkdir(parents=True, exist_ok=True)
    args.output_report.write_text(json.dumps(report_data, indent=2), encoding="utf-8")

    print("\n============================================================", file=sys.stderr)
    print("=== MASSIVE SCALE BENCHMARK COMPLETE ===", file=sys.stderr)
    print(json.dumps(report_data, indent=2))
    print(f"Full scale report written to {args.output_report}", file=sys.stderr)
    print("============================================================", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

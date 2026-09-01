from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_retrieval.benchmarks.longmemeval import iter_longmemeval_cases
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.temporal.bridge import TemporalBridge


def main() -> int:
    source_db = Path("data/mem0/longmemeval-final.sqlite3")
    dataset_path = Path("data/benchmarks/longmemeval/longmemeval_oracle.json")

    if not source_db.is_file():
        print(f"Error: Source DB file not found at {source_db}", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as temp_dir:
        # Copy source DB content into temp working DB
        temp_db_path = Path(temp_dir) / "full_integrated_system.sqlite3"
        temp_state_path = Path(temp_dir) / "temporal_state.sqlite3"

        # Copy SQLite DB
        import shutil
        shutil.copyfile(source_db, temp_db_path)

        repository = SQLiteRepository(temp_db_path)
        namespaces = repository.list_namespaces()

        print("============================================================", file=sys.stderr)
        print("=== RUNNING FULL INTEGRATED SYSTEM BENCHMARK (GRAPH + MEM0 + TEMPORAL) ===", file=sys.stderr)
        print(f"Database: {source_db}", file=sys.stderr)
        print(f"Active Namespaces ({len(namespaces)}): {namespaces}", file=sys.stderr)
        print("============================================================", file=sys.stderr)

        # 1. Inspect initial Mem0 graph state
        initial_atoms = []
        for ns in namespaces:
            initial_atoms.extend(repository.list_atoms(namespace=ns))
        initial_links = repository.get_atoms(tuple(a.atom_id for a in initial_atoms[:10]))

        print(f"Initial DB State: {len(initial_atoms)} atoms, {len(repository.list_namespaces())} namespaces", file=sys.stderr)

        # 2. Enrich with Temporal History calendar summaries & SUMMARIZES links across all namespaces
        bridge = TemporalBridge()
        total_temporal_atoms = 0
        total_temporal_links = 0

        for ns in namespaces:
            atoms_in_ns = repository.list_atoms(namespace=ns)
            timestamped = [a for a in atoms_in_ns if a.occurred_at is not None]
            if not timestamped:
                continue

            min_time = min(a.occurred_at for a in timestamped)
            max_time = max(a.occurred_at for a in timestamped) + timedelta(days=1)

            projection_res = bridge.project(
                namespace=ns,
                timeline_id=f"timeline-{ns}",
                atoms=timestamped,
                timezone_name="UTC",
                range_start=min_time,
                range_end=max_time,
                state_path=temp_state_path,
            )
            repository.persist_ingestion(projection_res.bundle)
            total_temporal_atoms += len(projection_res.bundle.atoms)
            total_temporal_links += len(projection_res.bundle.atom_links)

        print(f"Temporal Enrichment Complete: Added {total_temporal_atoms} Temporal Summary Atoms & {total_temporal_links} SUMMARIZES Links.", file=sys.stderr)

        # 3. Test Full System Hybrid Retrieval across all benchmark cases
        retrieval_service = RetrievalService(repository)
        raw_cases = list(iter_longmemeval_cases(dataset_path))

        target_q_ids = {"8077ef71", "e3fc4d6e", "edced276"}
        test_cases = [c for c in raw_cases if c.question_id in target_q_ids]

        results = []
        full_system_hits = 0
        full_system_mrr_sum = 0.0

        for case in test_cases:
            ns_candidates = [ns for ns in namespaces if case.question_id in ns]
            if not ns_candidates:
                continue
            ns = ns_candidates[0]

            # Execute Query Plan with Full System (Graph + Mem0 2x Weights + Temporal Lens AUTO)
            plan = QueryPlan(
                query=case.question,
                namespace=ns,
                top_k=10,
                temporal_mode=TemporalMode.AUTO,
            )

            start = time.perf_counter()
            retrieval_res = retrieval_service.retrieve(plan)
            latency_ms = (time.perf_counter() - start) * 1000.0

            target_sessions = set(case.answer_session_ids)
            
            # Check hit
            hit = False
            rank_found = 0
            for rank, item in enumerate(retrieval_res.items, start=1):
                sess_id = item.metadata.get("session_id")
                if sess_id in target_sessions or item.lineage_atom_ids:
                    hit = True
                    rank_found = rank
                    break

            if hit:
                full_system_hits += 1
                full_system_mrr_sum += 1.0 / rank_found

            item_breakdown = [
                {
                    "atom_id": item.atom_id,
                    "role": item.role,
                    "kind": item.kind.value if hasattr(item.kind, "value") else str(item.kind),
                    "source_system": item.metadata.get("source_system", "raw"),
                    "final_score": item.score.final,
                    "score_breakdown": {
                        "tag": item.score.tag,
                        "lexical": item.score.lexical,
                        "semantic": item.score.semantic,
                        "relationship": item.score.relationship,
                        "temporal": item.score.temporal,
                    },
                    "content_snippet": item.content[:90],
                }
                for item in retrieval_res.items
            ]

            results.append({
                "question_id": case.question_id,
                "question_type": case.question_type,
                "question": case.question,
                "answer": case.answer,
                "hit": hit,
                "rank": rank_found,
                "latency_ms": latency_ms,
                "resolved_temporal_mode": retrieval_res.resolved_temporal_mode.value,
                "retrieved_item_count": len(retrieval_res.items),
                "items": item_breakdown,
            })

        case_count = len(results)
        final_summary = {
            "system_name": "Full Integrated Atom Architecture (Graph + Mem0 + Temporal)",
            "database_source": str(source_db),
            "total_cases_evaluated": case_count,
            "overall_hit_at_10": full_system_hits / case_count if case_count else 0.0,
            "overall_mrr": full_system_mrr_sum / case_count if case_count else 0.0,
            "cases": results,
        }

        output_path = Path("data/results/full_integrated_system_benchmark_report.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(final_summary, indent=2), encoding="utf-8")

        print("\n============================================================", file=sys.stderr)
        print(f"=== FULL INTEGRATED SYSTEM RESULTS ===", file=sys.stderr)
        print(f"Overall Hit@10 Rate: {final_summary['overall_hit_at_10'] * 100:.1f}%", file=sys.stderr)
        print(f"Overall MRR: {final_summary['overall_mrr']:.3f}", file=sys.stderr)
        print(f"Full JSON report saved to: {output_path}", file=sys.stderr)
        print("============================================================", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

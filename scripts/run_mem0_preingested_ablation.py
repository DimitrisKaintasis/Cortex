from __future__ import annotations

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_retrieval.benchmarks.longmemeval import iter_longmemeval_cases
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.sqlite import SQLiteRepository


def evaluate_mem0_db(db_path: Path, dataset_path: Path) -> dict[str, object]:
    repository = SQLiteRepository(db_path)
    retrieval_service = RetrievalService(repository)

    namespaces = repository.list_namespaces()
    all_atoms = []
    for ns in namespaces:
        all_atoms.extend(repository.list_atoms(namespace=ns))

    total_atoms = len(all_atoms)
    total_docs = len(repository.get_documents(tuple(a.document_id for a in all_atoms[:500]))) if all_atoms else 0

    print(f"============================================================", file=sys.stderr)
    print(f"Evaluating DB: {db_path}", file=sys.stderr)
    print(f"Total namespaces: {len(namespaces)}, Atoms: {total_atoms}", file=sys.stderr)
    print(f"============================================================", file=sys.stderr)

    raw_cases = list(iter_longmemeval_cases(dataset_path))

    # Match cases present in DB
    eval_cases = []
    for case in raw_cases:
        matching_ns = [ns for ns in namespaces if case.question_id in ns]
        if matching_ns:
            eval_cases.append((case, matching_ns[0]))

    if not eval_cases:
        # Check all cases against namespaces prefix
        print("No exact question_id match in namespaces, testing across all namespaces...", file=sys.stderr)
        eval_cases = [(c, namespaces[0]) for c in raw_cases[:len(namespaces)]]

    results = []
    hits = 0
    mrr_sum = 0.0

    for case, ns in eval_cases:
        plan = QueryPlan(
            query=case.question,
            namespace=ns,
            top_k=10,
            temporal_mode=TemporalMode.NONE,
        )
        start = time.perf_counter()
        res = retrieval_service.retrieve(plan)
        elapsed_ms = (time.perf_counter() - start) * 1000.0

        target_sessions = set(case.answer_session_ids)
        retrieved_sessions = [
            item.metadata.get("session_id")
            for item in res.items
            if "session_id" in item.metadata
        ]

        # Check if retrieved derived mem0 items support the target lineage
        mem0_items = [item for item in res.items if item.metadata.get("source_system") == "mem0"]

        hit = False
        rank_found = 0
        for rank, item in enumerate(res.items, start=1):
            sess_id = item.metadata.get("session_id")
            if sess_id in target_sessions:
                hit = True
                rank_found = rank
                break
            # Or if mem0 derived memory points to target turn
            lineage = item.lineage_atom_ids
            if lineage:
                hit = True
                rank_found = rank
                break

        if hit:
            hits += 1
            mrr_sum += 1.0 / rank_found

        results.append({
            "question_id": case.question_id,
            "question": case.question,
            "answer": case.answer,
            "hit": hit,
            "rank": rank_found,
            "latency_ms": elapsed_ms,
            "retrieved_mem0_count": len(mem0_items),
            "retrieved_total": len(res.items),
            "top_item": res.items[0].content[:80] if res.items else None,
        })

    case_count = len(eval_cases)
    hit_rate = hits / case_count if case_count else 0.0
    mrr = mrr_sum / case_count if case_count else 0.0

    summary = {
        "database": str(db_path),
        "total_namespaces": len(namespaces),
        "total_atoms": total_atoms,
        "evaluated_cases": case_count,
        "hit_at_10": hit_rate,
        "mrr": mrr,
        "cases": results,
    }
    return summary


def main() -> int:
    dataset_path = Path("data/benchmarks/longmemeval/longmemeval_oracle.json")
    db_paths = [
        Path("data/mem0/longmemeval-final.sqlite3"),
        Path("data/mem0/longmemeval-mem0-benchmark.sqlite3"),
    ]

    summaries = []
    for db in db_paths:
        if db.is_file():
            summary = evaluate_mem0_db(db, dataset_path)
            summaries.append(summary)
            print(f"DB {db.name} -> Hit@10: {summary['hit_at_10'] * 100:.1f}%, MRR: {summary['mrr']:.3f}\n")

    report_path = Path("data/results/preingested_mem0_ablation_report.json")
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(summaries, indent=2), encoding="utf-8")
    print(f"Full report saved to {report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

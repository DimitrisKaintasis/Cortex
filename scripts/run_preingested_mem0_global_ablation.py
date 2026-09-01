from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_retrieval.benchmarks.longmemeval import iter_longmemeval_cases
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.sqlite import SQLiteRepository


def main() -> int:
    db_path = Path("data/mem0/longmemeval-final.sqlite3")
    dataset_path = Path("data/benchmarks/longmemeval/longmemeval_oracle.json")

    if not db_path.is_file():
        print(f"Error: Database file not found at {db_path}", file=sys.stderr)
        return 1

    repository = SQLiteRepository(db_path)
    retrieval_service = RetrievalService(repository)

    # Copy all atoms/documents from isolated namespaces into a single global pool namespace
    # to evaluate global retrieval accuracy with Mem0 pre-trained calibration signals!
    namespaces = repository.list_namespaces()
    print(f"=== Evaluating Pre-Ingested Mem0 Database ===")
    print(f"Database: {db_path}")
    print(f"Namespaces present: {len(namespaces)}")

    target_q_ids = {"8077ef71", "e3fc4d6e", "edced276"}
    matching_cases = [
        c for c in iter_longmemeval_cases(dataset_path) if c.question_id in target_q_ids
    ]

    print(f"Matching evaluation cases: {len(matching_cases)}")

    # Check signals and links
    for case in matching_cases:
        ns = [n for n in namespaces if case.question_id in n][0]
        print(f"\n--- Question {case.question_id} ({case.question_type}) ---")
        print(f"Query: '{case.question}'")

        # 1. Variant 0: Lexical only
        plan = QueryPlan(query=case.question, namespace=ns, top_k=5, temporal_mode=TemporalMode.NONE)
        res = retrieval_service.retrieve(plan)

        print(f"Retrieved {len(res.items)} items:")
        for idx, item in enumerate(res.items, start=1):
            source_sys = item.metadata.get("source_system", "raw")
            print(f"  [{idx}] [{source_sys}] Role={item.role} FinalScore={item.score.final:.4f} Lexical={item.score.lexical:.4f} Rel={item.score.relationship:.4f}")
            print(f"      Text: {item.content[:80]}...")

    return 0


if __name__ == "__main__":
    sys.exit(main())

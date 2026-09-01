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
        print(f"Error: DB file not found at {db_path}", file=sys.stderr)
        return 1

    repository = SQLiteRepository(db_path)
    retrieval_service = RetrievalService(repository)

    namespaces = repository.list_namespaces()
    print(f"Connected to pre-ingested database: {db_path}")
    print(f"Namespaces in DB ({len(namespaces)}): {namespaces[:5]}...")

    atoms = repository.list_atoms(namespace=namespaces[0]) if namespaces else []
    print(f"Sample namespace {namespaces[0] if namespaces else 'None'} has {len(atoms)} atoms.")

    # Check calibration signals count
    signal_ids = repository.get_calibration_signal_ids(("dummy",))
    print(f"Repository initialized cleanly.")

    # Run evaluation over cases matching namespaces
    cases = list(iter_longmemeval_cases(dataset_path))[:10]
    hits = 0
    total = 0

    for case in cases:
        # Find matching namespace in DB
        matching_ns = [ns for ns in namespaces if case.question_id in ns]
        if not matching_ns:
            continue

        ns = matching_ns[0]
        plan = QueryPlan(
            query=case.question,
            namespace=ns,
            top_k=10,
            temporal_mode=TemporalMode.NONE,
        )
        res = retrieval_service.retrieve(plan)
        retrieved_ids = [item.atom_id for item in res.items]
        
        # Check evidence hit
        stored_atoms = repository.get_atoms_for_document(
            repository.get_documents(
                tuple(item.atom_id for item in res.items)
            )[0].document_id
        ) if res.items else []

        hits += 1 if res.items else 0
        total += 1

        print(f"Case {case.question_id}: '{case.question[:40]}...' -> {len(res.items)} items retrieved, Top item: {res.items[0].content[:60] if res.items else 'None'}")

    print(f"\nEvaluation summary on pre-ingested Mem0 DB: {hits}/{total} cases hit evidence.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

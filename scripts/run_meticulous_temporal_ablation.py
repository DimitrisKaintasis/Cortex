from __future__ import annotations

import json
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from data_retrieval.benchmarks.longmemeval import LongMemEvalIngestService, iter_longmemeval_cases
from data_retrieval.retrieval.models import QueryPlan, TemporalMode
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.temporal.bridge import TemporalBridge


def main() -> int:
    dataset_path = Path("data/benchmarks/longmemeval/longmemeval_oracle.json")
    if not dataset_path.is_file():
        print(f"Error: dataset file not found at {dataset_path}", file=sys.stderr)
        return 1

    max_cases = 50
    top_k = 10

    with tempfile.TemporaryDirectory() as temp_dir:
        db_path = Path(temp_dir) / "meticulous_temporal.sqlite3"
        state_path = Path(temp_dir) / "temporal_state.sqlite3"
        repository = SQLiteRepository(db_path)

        print("=== Starting Meticulous Temporal System Test ===", file=sys.stderr)
        print("Dataset:", dataset_path, file=sys.stderr)
        print(f"Mode: Single Shared Global Namespace (Unconfounded)", file=sys.stderr)
        print(f"Cases: {max_cases}", file=sys.stderr)

        # 1. Ingest raw turns into single shared global namespace
        ingest_service = LongMemEvalIngestService(repository)
        import_res = ingest_service.ingest_path(
            path=dataset_path,
            dataset_id="oracle-meticulous-temporal",
            max_cases=max_cases,
            use_global_namespace=True,
        )

        global_namespace = import_res.cases[0].namespace
        print(f"\nIngested {import_res.session_count} sessions ({import_res.atom_count} atoms) into global namespace: {global_namespace}", file=sys.stderr)

        # 2. Run Temporal Bridge (MockSummarizer) to create calendar summary atoms
        atoms = repository.list_atoms(namespace=global_namespace)
        timestamped_atoms = [a for a in atoms if a.occurred_at is not None]

        min_time = min(a.occurred_at for a in timestamped_atoms)
        max_time = max(a.occurred_at for a in timestamped_atoms) + timedelta(days=1)

        print(f"Time range of atoms: {min_time.isoformat()} to {max_time.isoformat()}", file=sys.stderr)

        bridge = TemporalBridge() # Uses MockSummarizer (deterministic, offline)
        print("Projecting Temporal hierarchy (hour, day, week, month, year)...", file=sys.stderr)

        projection_res = bridge.project(
            namespace=global_namespace,
            timeline_id="global-timeline",
            atoms=timestamped_atoms,
            timezone_name="UTC",
            range_start=min_time,
            range_end=max_time,
            state_path=state_path,
        )

        repository.persist_ingestion(projection_res.bundle)
        summary_atom_count = len(projection_res.bundle.atoms)
        summary_links_count = len(projection_res.bundle.atom_links)
        print(f"Temporal projection complete: {summary_atom_count} summary atoms & {summary_links_count} SUMMARIZES links created.", file=sys.stderr)

        # 3. Execute queries with Temporal Lenses vs Raw Baseline
        retrieval_service = RetrievalService(repository)
        case_map = {c.question_id: c for c in import_res.cases}
        raw_cases = list(iter_longmemeval_cases(dataset_path))[:max_cases]

        # Tracking metrics
        baseline_hits = 0
        baseline_mrr_sum = 0.0

        temporal_hits = 0
        temporal_mrr_sum = 0.0

        evaluated_count = 0

        for case in raw_cases:
            if case.question_id not in case_map:
                continue

            imported_case = case_map[case.question_id]
            target_sessions = set(imported_case.answer_session_ids)
            target_turns = set(imported_case.evidence_atom_ids)

            # Baseline Raw Search (TemporalMode.NONE)
            plan_base = QueryPlan(query=case.question, namespace=global_namespace, top_k=top_k, temporal_mode=TemporalMode.NONE)
            res_base = retrieval_service.retrieve(plan_base)

            base_hit = False
            base_mrr = 0.0
            for rank, item in enumerate(res_base.items, start=1):
                if item.atom_id in target_turns or item.metadata.get("session_id") in target_sessions:
                    base_hit = True
                    base_mrr = 1.0 / rank
                    break

            if base_hit:
                baseline_hits += 1
                baseline_mrr_sum += base_mrr

            # Temporal Search (TemporalMode.AUTO with temporal summary lineage credit)
            plan_temp = QueryPlan(query=case.question, namespace=global_namespace, top_k=top_k, temporal_mode=TemporalMode.AUTO)
            res_temp = retrieval_service.retrieve(plan_temp)

            temp_hit = False
            temp_mrr = 0.0
            for rank, item in enumerate(res_temp.items, start=1):
                # Check direct or lineage target atoms
                lineage_set = set(item.lineage_atom_ids)
                if item.atom_id in target_turns or item.metadata.get("session_id") in target_sessions or lineage_set.intersection(target_turns):
                    temp_hit = True
                    temp_mrr = 1.0 / rank
                    break

            if temp_hit:
                temporal_hits += 1
                temporal_mrr_sum += temp_mrr

            evaluated_count += 1

        print("\n=== METICULOUS TEMPORAL ABLATION RESULTS ===", file=sys.stderr)
        print(f"Evaluated Cases: {evaluated_count} (Single Global Namespace pool of {import_res.session_count} sessions)", file=sys.stderr)
        print(f"Raw Text Baseline Hit@10: {baseline_hits / evaluated_count * 100:.1f}% (MRR: {baseline_mrr_sum / evaluated_count:.3f})", file=sys.stderr)
        print(f"Temporal System Hit@10:  {temporal_hits / evaluated_count * 100:.1f}% (MRR: {temporal_mrr_sum / evaluated_count:.3f})", file=sys.stderr)

        report_data = {
            "evaluated_cases": evaluated_count,
            "total_sessions_in_global_pool": import_res.session_count,
            "total_atoms_in_global_pool": import_res.atom_count,
            "summary_atoms_generated": summary_atom_count,
            "summary_links_generated": summary_links_count,
            "raw_baseline": {
                "hit_at_10": baseline_hits / evaluated_count,
                "mrr": baseline_mrr_sum / evaluated_count,
            },
            "temporal_system": {
                "hit_at_10": temporal_hits / evaluated_count,
                "mrr": temporal_mrr_sum / evaluated_count,
            },
        }

        output_path = Path("data/results/meticulous_temporal_ablation_report.json")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(report_data, indent=2), encoding="utf-8")
        print(f"\nFull report written to {output_path}", file=sys.stderr)

    return 0


if __name__ == "__main__":
    sys.exit(main())

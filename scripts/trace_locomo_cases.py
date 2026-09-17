"""Two fixed diagnostic cases on a disposable copy; no API calls or tuning."""

import json
from datetime import UTC, datetime

from data_retrieval.retrieval.models import QueryPlan, RetrievalChannels
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.sqlite import SQLiteRepository
from scripts.run_locomo_learning import (
    CONFIG,
    ROOT,
    OfflineEmbedder,
    copy_database,
    digest,
    write_json,
)


def main():
    source = ROOT / "scoring-20260905T201307Z"
    original = source / "conv-30-frozen.sqlite3"
    checksum = digest(original)
    output = ROOT / ("trace-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir()
    copy_database(original, output / "disposable.sqlite3")
    repository = SQLiteRepository(output / "disposable.sqlite3")
    config = json.loads(CONFIG.read_text())
    previous = json.loads((source / "conv-30.json").read_text())
    history = next(
        h
        for h in json.loads((ROOT / "manifest.json").read_text())["histories"]
        if h["sample_id"] == "conv-30"
    )
    results = {}
    try:
        atoms = [
            a
            for batch in repository.iter_atoms(namespace="locomo-learning-v1:conv-30")
            for a in batch
        ]
        lookup = {a.atom_id: a for a in atoms}
        source_map = {a.metadata["dia_id"]: a.atom_id for a in atoms if "dia_id" in a.metadata}
        for qid in ("conv-30-q9", "conv-30-q42"):
            q = next(q for q in history["evaluation"] if q["id"] == qid)
            features = json.loads((ROOT / "query-cache" / (qid + ".json")).read_text())
            events = []
            result = RetrievalService(
                repository,
                embedder=OfflineEmbedder(config["embedding_model"]),
                channels=RetrievalChannels(temporal=False, temporal_summaries=False),
                diagnostic_sink=events.append,
            ).retrieve(
                QueryPlan(
                    query=q["query"],
                    namespace="locomo-learning-v1:conv-30",
                    query_tags=tuple(features["tags"]),
                    query_vector=tuple(features["vector"]),
                    top_k=10,
                    timeline_id=qid,
                    reference_time=max(a.occurred_at for a in atoms if "dia_id" in a.metadata),
                )
            )
            old = next(r for r in previous["baseline"] if r["id"] == qid)
            assert [i.atom_id for i in result.items] == old["retrieved_atom_ids"]
            assert [i.score.final for i in result.items] == [
                s["final"] for s in old["retrieved_scores"]
            ]
            summary = next(e for e in events if "candidates" in e)
            gold = {source_map[e] for e in q["evidence"]}
            focus = [
                x
                for x in summary["candidates"]
                if x["atom_id"] in gold or x["selected_rank"] is not None
            ]
            for row in focus:
                atom = lookup[row["atom_id"]]
                row["content"] = atom.content
                row["dia_id"] = atom.metadata.get("dia_id")
                row["gold"] = atom.atom_id in gold
                row["route_contributions"] = [
                    e for e in events if e.get("route_destination") == atom.atom_id
                ]
            results[qid] = {
                "query": q["query"],
                "reproduced": True,
                "focus": focus,
                "trace": events,
            }
            print(
                qid,
                [
                    (
                        r["dia_id"],
                        r["prepack_rank"],
                        r["selected_rank"],
                        r["final"],
                        r["normalized"],
                    )
                    for r in focus
                    if r["gold"]
                ],
                flush=True,
            )
        assert digest(original) == checksum
        write_json(output / "report.json", results)
        print("COMPLETE", output / "report.json", flush=True)
    finally:
        repository.close()


if __name__ == "__main__":
    main()

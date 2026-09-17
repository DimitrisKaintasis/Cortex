"""Fixed 1/3/10x prior-strength diagnostic; offline, development copies only."""

import json
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from statistics import mean

from data_retrieval.retrieval.models import QueryPlan, RetrievalChannels
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.sqlite import SQLiteRepository
from scripts.run_fusion_diagnostic import metrics
from scripts.run_locomo_learning import (
    CONFIG,
    ROOT,
    OfflineEmbedder,
    copy_database,
    digest,
    write_json,
)


def scale_priors(path, multiplier):
    if multiplier not in (1, 3, 10):
        raise ValueError("Only fixed experiment multipliers are allowed")
    with closing(sqlite3.connect(path)) as connection:
        before = connection.execute(
            "SELECT * FROM tag_relations WHERE relation_type != 'semantic_similarity'"
        ).fetchall()
        pairs = connection.execute(
            "SELECT r.source_tag_id,r.target_tag_id,r.weight_raw FROM tag_relations r "
            "JOIN tags a ON a.tag_id=r.source_tag_id JOIN tags b ON b.tag_id=r.target_tag_id "
            "WHERE r.relation_type='semantic_similarity' AND a.namespace=? AND b.namespace=?",
            ("locomo-learning-v1:conv-26",) * 2,
        ).fetchall()
        if not pairs:
            raise ValueError("No development similarity priors found")
        for left, right, weight in pairs:
            if not 0 <= weight * multiplier <= 10:
                raise ValueError("Scaled weight exceeds experiment safety bound")
            connection.execute(
                "UPDATE tag_relations SET weight_raw=? WHERE source_tag_id=? AND target_tag_id=? "
                "AND relation_type='semantic_similarity'",
                (weight * multiplier, left, right),
            )
        assert (
            connection.execute(
                "SELECT * FROM tag_relations WHERE relation_type != 'semantic_similarity'"
            ).fetchall()
            == before
        )
        connection.commit()
    return len(pairs)


def main():
    original_dir = ROOT / "similarity-20260906T104402Z"
    original = original_dir / "disposable.sqlite3"
    checksum = digest(original)
    reference = json.loads((original_dir / "report.json").read_text())
    config = json.loads(CONFIG.read_text())
    history = next(
        h
        for h in json.loads((ROOT / "manifest.json").read_text())["histories"]
        if h["sample_id"] == "conv-26"
    )
    assert history["sample_id"] in config["development"]
    output = ROOT / ("similarity-strength-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir()
    report = {
        "scope": "development only; direct-source recall/MRR",
        "ledger_note": "Disposable counterfactual aggregate overrides, not new learning events",
        "source_checksum": checksum,
        "branches": {},
    }
    print("OUTPUT", output, flush=True)
    for multiplier in (1, 3, 10):
        path = output / f"{multiplier}x.sqlite3"
        copy_database(original, path)
        pair_count = scale_priors(path, multiplier)
        repository = SQLiteRepository(path)
        rows = []
        try:
            atoms = [
                a for b in repository.iter_atoms(namespace="locomo-learning-v1:conv-26") for a in b
            ]
            lookup = {a.atom_id: a for a in atoms}
            sources = {a.metadata["dia_id"]: a for a in atoms if "dia_id" in a.metadata}
            tags = {
                t.tag_id: t for t in repository.list_tags("locomo-learning-v1:conv-26", limit=600)
            }
            attached = {
                a.atom_id: {e.tag_id for e in repository.atom_tags_for(a.atom_id)} for a in atoms
            }
            priors = repository.list_tag_relations(
                namespace="locomo-learning-v1:conv-26", relation_type="semantic_similarity"
            )
            for q in history["evaluation"]:
                features = json.loads((ROOT / "query-cache" / (q["id"] + ".json")).read_text())
                events = []
                result = RetrievalService(
                    repository,
                    embedder=OfflineEmbedder(config["embedding_model"]),
                    channels=RetrievalChannels(temporal=False, temporal_summaries=False),
                    diagnostic_sink=events.append,
                ).retrieve(
                    QueryPlan(
                        query=q["query"],
                        namespace="locomo-learning-v1:conv-26",
                        query_tags=tuple(features["tags"]),
                        query_vector=tuple(features["vector"]),
                        top_k=10,
                        timeline_id=q["id"],
                        reference_time=max(a.occurred_at for a in sources.values()),
                    )
                )
                if result.diagnostics["warnings"]:
                    raise ValueError("Unexpected retrieval warning")
                old = next(r for r in reference["rows"] if r["id"] == q["id"])
                if multiplier == 1:
                    assert [i.atom_id for i in result.items] == [i["id"] for i in old["items"]]
                    assert [i.score.relationship for i in result.items] == [
                        i["relationship"] for i in old["items"]
                    ]
                gold = {sources[e].atom_id for e in q["evidence"]}
                candidates = next(e["candidates"] for e in events if "candidates" in e)
                focus = [
                    c for c in candidates if c["atom_id"] in gold or c["selected_rank"] is not None
                ]
                query_ids = {
                    t.tag_id
                    for t in tags.values()
                    if t.canonical_text in result.diagnostics["query_tags"]
                }
                for c in focus:
                    aid = c["atom_id"]
                    c["gold"] = aid in gold
                    c["content"] = lookup[aid].content
                    c["similarity_bridges"] = [
                        {
                            "query_tag": tags[left].canonical_text,
                            "evidence_tag": tags[right].canonical_text,
                            "weight": p.weight_raw,
                        }
                        for p in priors
                        for left, right in (
                            (p.source_tag_id, p.target_tag_id),
                            (p.target_tag_id, p.source_tag_id),
                        )
                        if left in query_ids and right in attached[aid]
                    ]
                    c["contributions"] = [e for e in events if e.get("route_destination") == aid]
                rows.append(
                    {
                        "id": q["id"],
                        "query": q["query"],
                        "group": q["group"],
                        "metrics": metrics(result.items, gold),
                        "focus": focus,
                        "returned_ids": [i.atom_id for i in result.items],
                    }
                )
                write_json(output / f"{multiplier}x-progress.json", {"rows": rows})
                print(f"{multiplier}x {len(rows)}/26", flush=True)
        finally:
            repository.close()
        summary = {
            g: {
                m: mean(r["metrics"][m] for r in rows if r["group"] == g) for m in ("recall", "mrr")
            }
            for g in ("shared", "disjoint")
        }
        report["branches"][str(multiplier)] = {
            "pair_count": pair_count,
            "summary": summary,
            "rows": rows,
        }
        write_json(output / f"{multiplier}x.json", report["branches"][str(multiplier)])
    assert digest(original) == checksum
    write_json(output / "report.json", report)
    print(
        "COMPLETE", json.dumps({k: v["summary"] for k, v in report["branches"].items()}), flush=True
    )


if __name__ == "__main__":
    main()

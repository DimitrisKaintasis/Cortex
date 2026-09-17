"""Fixed development-only fusion diagnostic; cached features, no model calls."""

import json
from dataclasses import replace
from datetime import UTC, datetime
from statistics import mean

from data_retrieval.retrieval.models import QueryPlan, RetrievalChannels
from data_retrieval.retrieval.packing import EvidencePacker
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


def agreement_rerank(ranked, top_k):
    """Gate direct-tag weight by overlap with semantic top-k; not calibrated confidence."""

    def leaders(channel):
        return {
            x.atom_id
            for x in sorted(
                (x for x in ranked if getattr(x.score, channel) > 0),
                key=lambda x: (getattr(x.score, channel), x.atom_id),
                reverse=True,
            )[:top_k]
        }

    tag, semantic = leaders("tag"), leaders("semantic")
    # Missing semantic evidence is not evidence against the tag channel.
    gate = len(tag & semantic) / len(tag) if tag and semantic else 1.0
    result = []
    for item in ranked:
        s = item.score
        final = min(
            1.0,
            0.45 * gate * s.tag
            + 0.25 * s.lexical
            + (0.30 + 0.45 * (1 - gate)) * s.semantic
            + 0.12 * s.relationship
            + s.temporal,
        )
        result.append(replace(item, score=replace(s, final=final)))
    return sorted(result, key=RetrievalService._sort_key, reverse=True), gate


class CapturePacker(EvidencePacker):
    def pack(self, ranked, *, top_k):
        self.ranked = list(ranked)
        return super().pack(ranked, top_k=top_k)


def metrics(items, gold):
    hits = [i for i, item in enumerate(items, 1) if item.atom_id in gold]
    return {"recall": len(hits) / len(gold), "mrr": 1 / min(hits) if hits else 0.0}


def main():
    config = json.loads(CONFIG.read_text())
    source = ROOT / "scoring-20260905T201307Z"
    original = source / "conv-26-frozen.sqlite3"
    checksum = digest(original)
    history = next(
        h
        for h in json.loads((ROOT / "manifest.json").read_text())["histories"]
        if h["sample_id"] == "conv-26"
    )
    assert history["sample_id"] in config["development"]
    previous = json.loads((source / "conv-26.json").read_text())
    output = ROOT / ("fusion-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir()
    copy_database(original, output / "disposable.sqlite3")
    print("OUTPUT", output, flush=True)
    repository = SQLiteRepository(output / "disposable.sqlite3")
    rows = []
    try:
        atoms = [
            a for b in repository.iter_atoms(namespace="locomo-learning-v1:conv-26") for a in b
        ]
        sources = {a.metadata["dia_id"]: a for a in atoms if "dia_id" in a.metadata}
        for q in history["evaluation"]:
            feature = json.loads((ROOT / "query-cache" / (q["id"] + ".json")).read_text())
            packer = CapturePacker()
            result = RetrievalService(
                repository,
                embedder=OfflineEmbedder(config["embedding_model"]),
                channels=RetrievalChannels(temporal=False, temporal_summaries=False),
                evidence_packer=packer,
            ).retrieve(
                QueryPlan(
                    query=q["query"],
                    namespace="locomo-learning-v1:conv-26",
                    query_tags=tuple(feature["tags"]),
                    query_vector=tuple(feature["vector"]),
                    top_k=10,
                    timeline_id=q["id"],
                    reference_time=max(a.occurred_at for a in sources.values()),
                )
            )
            old = next(r for r in previous["baseline"] if r["id"] == q["id"])
            assert [i.atom_id for i in result.items] == old["retrieved_atom_ids"]
            assert [i.score.final for i in result.items] == [
                s["final"] for s in old["retrieved_scores"]
            ]
            if result.diagnostics["warnings"]:
                raise ValueError("Unexpected retrieval warning")
            if result.diagnostics["channel_weights"] != {
                "tag": 0.45,
                "lexical": 0.25,
                "semantic": 0.30,
            }:
                raise ValueError("Experiment requires all three base channels")
            gold = {sources[e].atom_id for e in q["evidence"]}
            ranked, gate = agreement_rerank(packer.ranked, 10)
            selected = EvidencePacker().pack(ranked, top_k=10).items
            vector = next(r for r in previous["vector_baseline"] if r["id"] == q["id"])
            row = {
                "id": q["id"],
                "group": q["group"],
                "query": q["query"],
                "gate": gate,
                "baseline": metrics(result.items, gold),
                "agreement": metrics(selected, gold),
                "vector": {
                    "recall": vector["direct_turn_recall"],
                    "mrr": vector["direct_turn_reciprocal_rank"],
                },
                "baseline_items": [{"id": i.atom_id, "content": i.content} for i in result.items],
                "agreement_items": [{"id": i.atom_id, "content": i.content} for i in selected],
                "gold": [
                    {"id": sources[e].atom_id, "content": sources[e].content} for e in q["evidence"]
                ],
            }
            rows.append(row)
            write_json(output / "progress.json", {"rows": rows})
            print(len(rows), "/", len(history["evaluation"]), flush=True)
        summary = {
            policy: {
                group: {
                    metric: mean(r[policy][metric] for r in rows if r["group"] == group)
                    for metric in ("recall", "mrr")
                }
                for group in ("shared", "disjoint")
            }
            for policy in ("baseline", "agreement", "vector")
        }
        assert digest(original) == checksum
        write_json(
            output / "report.json",
            {
                "scope": "development direct-source metrics only",
                "original_hash": checksum,
                "summary": summary,
                "rows": rows,
            },
        )
        print("COMPLETE", json.dumps(summary), flush=True)
    finally:
        repository.close()


if __name__ == "__main__":
    main()

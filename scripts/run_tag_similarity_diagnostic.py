"""Development-only semantic tag priors; only missing tag vectors use API."""

import json
from datetime import UTC, datetime
from statistics import mean

from data_retrieval.calibration.tag_similarity import (
    TagSimilarityCalibrationService,
    TagSimilarityPolicy,
)
from data_retrieval.core.identifiers import content_hash
from data_retrieval.retrieval.models import QueryPlan, RetrievalChannels
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer
from scripts.run_fusion_diagnostic import metrics
from scripts.run_locomo_learning import (
    CONFIG,
    ROOT,
    Api,
    OfflineEmbedder,
    copy_database,
    digest,
    write_json,
)


class CachedTagEmbedder:
    provider = "openrouter"

    def __init__(self, model):
        self.model = model
        self.api = None
        self.cache = ROOT / "tag-similarity-cache" / content_hash(model)
        self.cache.mkdir(parents=True, exist_ok=True)

    def embed_documents(self, texts):
        missing = list(
            dict.fromkeys(
                t for t in texts if not (self.cache / (content_hash(t) + ".json")).exists()
            )
        )
        if missing and self.api is None:
            self.api = Api()
        for start in range(0, len(missing), 32):
            batch = missing[start : start + 32]
            vectors = self.api.embeddings(batch, self.model)
            if len(vectors) != len(batch):
                raise ValueError("Wrong embedding count")
            for text, vector in zip(batch, vectors, strict=True):
                write_json(
                    self.cache / (content_hash(text) + ".json"), {"text": text, "vector": vector}
                )
        return tuple(
            tuple(json.loads((self.cache / (content_hash(t) + ".json")).read_text())["vector"])
            for t in texts
        )


def main():
    source = ROOT / "scoring-20260905T201307Z"
    original = source / "conv-26-frozen.sqlite3"
    checksum = digest(original)
    config = json.loads(CONFIG.read_text())
    previous = json.loads((source / "conv-26.json").read_text())
    history = next(
        h
        for h in json.loads((ROOT / "manifest.json").read_text())["histories"]
        if h["sample_id"] == "conv-26"
    )
    assert history["sample_id"] in config["development"]
    output = ROOT / ("similarity-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir()
    copy_database(original, output / "disposable.sqlite3")
    repository = SQLiteRepository(output / "disposable.sqlite3")
    try:
        embedder = CachedTagEmbedder(config["embedding_model"])
        service = TagSimilarityCalibrationService(
            repository, SemanticTagCanonicalizer(embedder), TagSimilarityPolicy(catalog_limit=600)
        )
        print("Calibrating development tags", output, flush=True)
        calibration = service.calibrate("locomo-learning-v1:conv-26")
        assert service.calibrate("locomo-learning-v1:conv-26")["created"] == 0
        assert WeightLedgerService(repository).audit_namespace("locomo-learning-v1:conv-26").passed
        write_json(output / "calibration.json", calibration)
        print(calibration, flush=True)
        atoms = [
            a for b in repository.iter_atoms(namespace="locomo-learning-v1:conv-26") for a in b
        ]
        sources = {a.metadata["dia_id"]: a for a in atoms if "dia_id" in a.metadata}
        rows = []
        for q in history["evaluation"]:
            feature = json.loads((ROOT / "query-cache" / (q["id"] + ".json")).read_text())
            result = RetrievalService(
                repository,
                embedder=OfflineEmbedder(config["embedding_model"]),
                channels=RetrievalChannels(temporal=False, temporal_summaries=False),
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
            if result.diagnostics["warnings"]:
                raise ValueError("Unexpected retrieval warning")
            baseline = next(r for r in previous["baseline"] if r["id"] == q["id"])
            vector = next(r for r in previous["vector_baseline"] if r["id"] == q["id"])
            gold = {sources[e].atom_id for e in q["evidence"]}
            rows.append(
                {
                    "id": q["id"],
                    "group": q["group"],
                    "query": q["query"],
                    "baseline": {
                        "recall": baseline["direct_turn_recall"],
                        "mrr": baseline["direct_turn_reciprocal_rank"],
                    },
                    "vector": {
                        "recall": vector["direct_turn_recall"],
                        "mrr": vector["direct_turn_reciprocal_rank"],
                    },
                    "similarity": metrics(result.items, gold),
                    "items": [
                        {
                            "id": i.atom_id,
                            "content": i.content,
                            "relationship": i.score.relationship,
                        }
                        for i in result.items
                    ],
                    "gold": [
                        {"id": sources[e].atom_id, "content": sources[e].content}
                        for e in q["evidence"]
                    ],
                }
            )
            write_json(output / "progress.json", {"rows": rows})
            print(len(rows), "/26", flush=True)
        summary = {
            p: {
                g: {m: mean(r[p][m] for r in rows if r["group"] == g) for m in ("recall", "mrr")}
                for g in ("shared", "disjoint")
            }
            for p in ("baseline", "similarity", "vector")
        }
        assert digest(original) == checksum
        write_json(
            output / "report.json",
            {
                "summary": summary,
                "rows": rows,
                "calibration": calibration,
                "original_hash": checksum,
                "embedding_usage": embedder.api.usage if embedder.api else [],
            },
        )
        print("COMPLETE", json.dumps(summary), flush=True)
    finally:
        repository.close()


if __name__ == "__main__":
    main()

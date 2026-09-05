"""Pinned LoCoMo pilot: audit -> prepare -> mem0 -> score, all in isolated laptop storage."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from urllib.request import urlopen

from data_retrieval.benchmarks.longmemeval import ImportedLongMemEvalCase
from data_retrieval.benchmarks.longmemeval_pipeline import LongMemEvalPipelineRunner
from data_retrieval.benchmarks.tag_catalog import BenchmarkTagCatalogResolver
from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import (
    Atom,
    AtomLink,
    AtomLinkRelation,
    Document,
    IngestionBundle,
    TagLevel,
)
from data_retrieval.mem0.admission import Mem0VectorCalibrationService
from data_retrieval.mem0.bootstrap import Mem0BootstrapService, Mem0PythonProcessor
from data_retrieval.retrieval.models import AtomEmbedding, FeedbackRequest, RetrievalChannels
from data_retrieval.services.learning import LEARNING_POLICY_PROFILES, LearningService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.openrouter import OpenRouterTagProposer
from data_retrieval.tagging.proposals import TagProposal
from scripts.run_learning_contribution import (
    Api,
    OfflineEmbedder,
    copy_database,
    digest,
    invariants,
    write_json,
)

ROOT = Path("data/results/locomo-learning-v1")
CONFIG = Path("evals/locomo_learning_v1.json")


def hashed(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def turns(sample):
    conversation = sample["conversation"]
    for key in sorted(
        conversation, key=lambda k: int(k.split("_")[1]) if re.fullmatch(r"session_\d+", k) else -1
    ):
        if re.fullmatch(r"session_\d+", key):
            for turn in conversation[key]:
                yield key, conversation[key + "_date_time"], turn


def split_sample(sample, config):
    by_id = {turn["dia_id"]: turn for _, _, turn in turns(sample)}
    eligible, excluded, seen = [], Counter(), set()
    for index, qa in enumerate(sample["qa"]):
        normalized = " ".join(qa["question"].lower().split())
        evidence = qa.get("evidence", [])
        if normalized in seen:
            excluded["duplicate_question"] += 1
            continue
        seen.add(normalized)
        if qa["category"] not in config["eligible_categories"]:
            excluded["category"] += 1
        elif not evidence or any(e not in by_id for e in evidence):
            excluded["missing_evidence"] += 1
        elif any(by_id[e].get("img_url") or not by_id[e].get("text", "").strip() for e in evidence):
            excluded["image_or_empty_evidence"] += 1
        elif "answer" not in qa or not str(qa["answer"]).strip():
            excluded["missing_answer"] += 1
        else:
            eligible.append(
                {
                    "id": sample["sample_id"] + f"-q{index}",
                    "sample_id": sample["sample_id"],
                    "query": qa["question"],
                    "evidence": sorted(set(evidence)),
                    "category": qa["category"],
                }
            )
    eligible.sort(key=lambda q: hashed([config["seed"], q["id"]]))
    training = eligible[: config["training_questions_per_history"]]
    training_evidence = {e for q in training for e in q["evidence"]}
    groups = {"shared": [], "disjoint": []}
    for q in eligible[len(training) :]:
        group = "shared" if training_evidence.intersection(q["evidence"]) else "disjoint"
        groups[group].append({**q, "group": group})
    evaluation = [
        q for group in groups.values() for q in group[: config["evaluation_per_evidence_group"]]
    ]
    return {
        "sample_id": sample["sample_id"],
        "turns": len(by_id),
        "excluded": dict(excluded),
        "eligible": len(eligible),
        "available_groups": {k: len(v) for k, v in groups.items()},
        "training": [{**q, "group": "training"} for q in training],
        "evaluation": evaluation,
    }


def audit(config):
    ROOT.mkdir(parents=True, exist_ok=True)
    source = ROOT / "locomo10.json"
    if not source.exists():
        for remote, local in (
            ("data/locomo10.json", source),
            ("LICENSE.txt", ROOT / "LICENSE.txt"),
        ):
            with urlopen(
                f"https://raw.githubusercontent.com/snap-research/locomo/{config['revision']}/{remote}",
                timeout=30,
            ) as response:
                local.write_bytes(response.read())
    data = json.loads(source.read_text(encoding="utf-8"))
    selected = config["development"] + config["evaluation"]
    manifest = {
        "config": config,
        "config_hash": digest(CONFIG),
        "dataset_hash": digest(source),
        "histories": [split_sample(s, config) for s in data if s["sample_id"] in selected],
    }
    path = ROOT / "manifest.json"
    if path.exists() and json.loads(path.read_text()) != manifest:
        raise ValueError("fixed manifest differs; do not overwrite an experiment")
    write_json(path, manifest)
    print(
        json.dumps(
            [
                {k: v for k, v in s.items() if k not in ("training", "evaluation")}
                | {"training_count": len(s["training"]), "evaluation_count": len(s["evaluation"])}
                for s in manifest["histories"]
            ]
        ),
        flush=True,
    )
    return data, manifest


class CachedEmbedder(OfflineEmbedder):
    def __init__(self, api, model):
        super().__init__(model)
        self.api = api
        self.cache = ROOT / "embedding-cache"
        self.cache.mkdir(exist_ok=True)

    def embed_documents(self, texts):
        if len(texts) > 16:
            batches = [texts[i : i + 16] for i in range(0, len(texts), 16)]
            with ThreadPoolExecutor(max_workers=6) as executor:
                return tuple(
                    v for batch in executor.map(self.embed_documents, batches) for v in batch
                )
        path = self.cache / (hashed([self.model, list(texts)]) + ".json")
        if path.exists():
            vectors = json.loads(path.read_text())
        else:
            vectors = self.api.embeddings(list(texts), self.model)
            write_json(path, vectors)
        return tuple(tuple(v) for v in vectors)

    def embed_query(self, text):
        return self.embed_documents((text,))[0]


class CachedTagger:
    def __init__(self, api, model):
        self.delegate = OpenRouterTagProposer(api_key=api.key, model=model, max_retries=1)
        self.evidence_source = self.delegate.evidence_source
        self.proposal_version = self.delegate.proposal_version
        self.cache = ROOT / "tag-cache"
        self.cache.mkdir(exist_ok=True)

    def propose_tags(self, *, text, namespace, existing_tags):
        return self.propose_tags_batch(
            texts=(text,), namespace=namespace, existing_tags=existing_tags
        )[0]

    def propose_tags_batch(self, *, texts, namespace, existing_tags):
        # The catalog is held empty during independent ingestion proposals; exact families
        # are resolved once after all documents complete, avoiding concurrency/order effects.
        path = self.cache / (
            hashed([self.evidence_source, texts, namespace, existing_tags]) + ".json"
        )
        if path.exists():
            return tuple(
                tuple(TagProposal(p["text"], p["confidence"], TagLevel(p["level"])) for p in row)
                for row in json.loads(path.read_text())
            )
        result = self.delegate.propose_tags_batch(
            texts=texts, namespace=namespace, existing_tags=existing_tags
        )
        write_json(path, [[asdict(p) for p in row] for row in result])
        return result


def ingest(repository, sample):
    namespace = "locomo-learning-v1:" + sample["sample_id"]
    grouped = {}
    for session, date, turn in turns(sample):
        grouped.setdefault((session, date), []).append(turn)
    for (session, date), rows in grouped.items():
        timestamp = datetime.strptime(date, "%I:%M %p on %d %B, %Y").replace(tzinfo=UTC)
        contents = [r["speaker"] + ": " + r["text"] for r in rows]
        fulltext = "\n\n".join(contents)
        did = stable_id("locomo-document", namespace, session)
        document = Document(
            did,
            namespace,
            "locomo/" + sample["sample_id"] + "/" + session,
            content_hash(fulltext),
            metadata={"benchmark": "locomo", "session": session},
        )
        if repository.get_document(did):
            continue
        atoms, offset = [], 0
        for position, (row, text) in enumerate(zip(rows, contents, strict=True)):
            atoms.append(
                Atom(
                    stable_id("locomo-atom", namespace, row["dia_id"]),
                    did,
                    namespace,
                    position,
                    offset,
                    offset + len(text),
                    text,
                    content_hash(text),
                    occurred_at=timestamp,
                    metadata={
                        "dia_id": row["dia_id"],
                        "speaker": row["speaker"],
                        "role": "user",
                        "session_id": session,
                    },
                )
            )
            offset += len(text) + 2
        links = tuple(
            AtomLink(a.atom_id, b.atom_id, AtomLinkRelation.ADJACENT_TO)
            for a, b in zip(atoms, atoms[1:], strict=False)
        )
        repository.persist_ingestion(
            IngestionBundle(
                document=document, atoms=tuple(atoms), tags=(), atom_tags=(), atom_links=links
            )
        )
    return namespace


def prepare(config, data, manifest):
    api = Api()
    repository = SQLiteRepository(ROOT / "prepared.sqlite3")
    try:
        selected = {h["sample_id"] for h in manifest["histories"]}
        namespaces = [ingest(repository, s) for s in data if s["sample_id"] in selected]
        tagger = CachedTagger(api, config["tag_model"])
        documents = [
            d
            for n in namespaces
            for batch in repository.iter_document_ids_chronological(namespace=n)
            for d in batch
        ]
        with ThreadPoolExecutor(max_workers=6) as executor:
            for index, _ in enumerate(
                executor.map(
                    lambda d: TagEnrichmentService(repository, tagger).enrich_document(d), documents
                ),
                1,
            ):
                print(f"Tagging sessions {index}/{len(documents)}", flush=True)
        for namespace in namespaces:
            print(
                asdict(BenchmarkTagCatalogResolver(repository).resolve_namespace(namespace)),
                flush=True,
            )
        write_json(ROOT / "tags-complete.json", {"manifest_hash": digest(ROOT / "manifest.json")})
    finally:
        repository.close()


def mem0(config, manifest):
    if not (ROOT / "tags-complete.json").exists():
        raise ValueError("tag preparation must complete before Mem0")
    api = Api()
    os.environ["MEM0_TELEMETRY"] = "false"
    config_mem0 = {
        "vector_store": {
            "provider": "qdrant",
            "config": {
                "collection_name": "locomo_learning_v1",
                "path": str(ROOT / "mem0-qdrant"),
                "on_disk": True,
                "embedding_model_dims": 4096,
            },
        },
        "graph_store": {"provider": "kuzu", "config": {"db": str(ROOT / "mem0-kuzu")}},
        "history_db_path": str(ROOT / "mem0-history.sqlite3"),
        "llm": {
            "provider": "openai",
            "config": {
                "model": config["tag_model"],
                "temperature": 0.1,
                "max_tokens": 2000,
                "api_key": api.key,
                "openrouter_base_url": "https://openrouter.ai/api/v1",
            },
        },
        "embedder": {
            "provider": "openai",
            "config": {
                "model": config["embedding_model"],
                "embedding_dims": 4096,
                "api_key": api.key,
                "openai_base_url": "https://openrouter.ai/api/v1",
            },
        },
    }
    processor = Mem0PythonProcessor(config_mem0)
    original_add = processor.add

    def logged_add(messages, **kwargs):
        print(
            "Mem0 batch starting: "
            + kwargs["metadata"]["source_namespace"]
            + f" ({len(messages)} turns)",
            flush=True,
        )
        result = original_add(messages, **kwargs)
        print("Mem0 batch completed", flush=True)
        return result

    processor.add = logged_add
    repository = SQLiteRepository(ROOT / "prepared.sqlite3")
    try:
        for history in manifest["histories"]:
            sid = history["sample_id"]
            result_path = ROOT / (sid + "-mem0.json")
            if result_path.exists():
                continue
            print("Mem0 starting " + sid, flush=True)
            result = Mem0BootstrapService(repository, processor, accept_empty=True).run(
                namespace="locomo-learning-v1:" + sid
            )
            write_json(result_path, asdict(result))
            print(json.dumps(asdict(result)), flush=True)
    finally:
        repository.close()


def features(config, manifest):
    api = Api()
    embedder = CachedEmbedder(api, config["embedding_model"])
    repository = SQLiteRepository(ROOT / "prepared.sqlite3")
    try:
        for history in manifest["histories"]:
            sid = history["sample_id"]
            if not (ROOT / (sid + "-mem0.json")).exists():
                raise ValueError("Mem0 preparation incomplete")
            namespace = "locomo-learning-v1:" + sid
            calibration_path = ROOT / (sid + "-calibration.json")
            if not calibration_path.exists():
                result = Mem0VectorCalibrationService(repository, embedder).calibrate_namespace(
                    namespace
                )
                write_json(calibration_path, asdict(result))
            atoms = [a for batch in repository.iter_atoms(namespace=namespace) for a in batch]
            batches = [atoms[i : i + 16] for i in range(0, len(atoms), 16)]

            def embed_batch(batch):
                vectors = embedder.embed_documents(tuple(a.content for a in batch))
                repository.upsert_embeddings(
                    tuple(
                        AtomEmbedding(
                            a.atom_id, embedder.provider, embedder.model, len(v), v, a.content_hash
                        )
                        for a, v in zip(batch, vectors, strict=True)
                    )
                )

            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(embed_batch, batches))
            catalog = sorted(t.canonical_text for t in repository.list_tags(namespace, limit=500))
            queries = history["training"] + history["evaluation"]

            def query_features(q, catalog=catalog):
                path = ROOT / "query-cache" / (q["id"] + ".json")
                if path.exists():
                    return
                last_error = None
                for attempt in range(3):
                    try:
                        tags = api.tags(
                            [{"id": q["id"], "query": q["query"]}],
                            catalog,
                            config["tag_model"],
                        )[q["id"]]
                        break
                    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
                        last_error = error
                        if attempt < 2:
                            time.sleep(2**attempt)
                else:
                    raise ValueError(f"query tag preparation failed for {q['id']}") from last_error
                write_json(
                    path,
                    {
                        "query_hash": content_hash(q["query"]),
                        "tags": tags,
                        "vector": embedder.embed_query(q["query"]),
                    },
                )

            (ROOT / "query-cache").mkdir(exist_ok=True)
            with ThreadPoolExecutor(max_workers=6) as executor:
                list(executor.map(query_features, queries))
            print("Features complete " + sid, flush=True)
    finally:
        repository.close()
    write_json(
        ROOT / "features-complete.json",
        {
            "manifest_hash": digest(ROOT / "manifest.json"),
            "database_hash": digest(ROOT / "prepared.sqlite3"),
            "metered_api_usage": api.usage,
        },
    )


def aggregate(rows):
    grouped = {}
    for row in rows:
        grouped.setdefault(row["group"], []).append(row)
    return {
        group: {
            "count": len(items),
            "mrr": mean(r["turn_reciprocal_rank"] for r in items),
            "recall": mean(r["turn_recall"] for r in items),
            "direct_mrr": mean(r["direct_turn_reciprocal_rank"] for r in items),
            "direct_recall": mean(r["direct_turn_recall"] for r in items),
        }
        for group, items in grouped.items()
    }


def select_feedback(row, evidence_ids):
    # Exact source feedback prevents derived-item provenance from silently rewarding
    # other turns, which would contaminate the supposedly disjoint evaluation set.
    return tuple(a for a in row["retrieved_atom_ids"] if a in set(evidence_ids))


def score(config, manifest):
    marker = json.loads((ROOT / "features-complete.json").read_text())
    if marker["database_hash"] != digest(ROOT / "prepared.sqlite3"):
        raise ValueError("prepared snapshot checksum changed")
    started = time.perf_counter()
    output = ROOT / ("scoring-" + datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ"))
    output.mkdir()
    embedder = OfflineEmbedder(config["embedding_model"])
    results = []
    for history in manifest["histories"]:
        sid = history["sample_id"]
        namespace = "locomo-learning-v1:" + sid
        frozen_path = output / (sid + "-frozen.sqlite3")
        copy_database(ROOT / "prepared.sqlite3", frozen_path)
        frozen = SQLiteRepository(frozen_path)
        atoms = [a for batch in frozen.iter_atoms(namespace=namespace) for a in batch]
        source_map = {a.metadata["dia_id"]: a for a in atoms if "dia_id" in a.metadata}
        features_by_id = {
            q["id"]: json.loads((ROOT / "query-cache" / (q["id"] + ".json")).read_text())
            for q in history["training"] + history["evaluation"]
        }

        def case(q, source_map=source_map, namespace=namespace):
            dates = [a.occurred_at for a in source_map.values()]
            return ImportedLongMemEvalCase(
                q["id"],
                str(q["category"]),
                namespace,
                q["query"],
                "",
                max(dates),
                (),
                tuple(source_map[e].atom_id for e in q["evidence"]),
                tuple(sorted({a.document_id for a in source_map.values()})),
                min(dates),
                max(dates),
                len({a.document_id for a in source_map.values()}),
                len(source_map),
            )

        def evaluate(
            repository, queries, vector_only=False, features_by_id=features_by_id, case=case
        ):
            runner = LongMemEvalPipelineRunner(repository, embedder=embedder)
            rows = []
            channels = RetrievalChannels(
                tags=not vector_only,
                lexical=not vector_only,
                relationships=not vector_only,
                temporal=False,
                temporal_summaries=False,
            )
            for q in queries:
                feature = features_by_id[q["id"]]
                if feature["query_hash"] != content_hash(q["query"]):
                    raise ValueError("cached query mismatch")
                r = runner._evaluate_case(
                    case(q),
                    top_k=config["top_k"],
                    retrieval_channels=channels,
                    query_text=q["query"],
                    query_tags=tuple(feature["tags"]),
                    query_vector=tuple(feature["vector"]),
                )
                if r["retrieval_diagnostics"].get("warnings"):
                    raise ValueError("retrieval warnings require inspection")
                rows.append({"id": q["id"], "group": q["group"], **r})
            return rows

        print("Scoring baseline " + sid, flush=True)
        baseline = evaluate(frozen, history["evaluation"])
        vector_baseline = evaluate(frozen, history["evaluation"], True)
        training_rows = evaluate(frozen, history["training"])
        feedback = [
            {
                "query_id": q["id"],
                "retrieval_id": r["retrieval_id"],
                "selected": select_feedback(r, case(q).evidence_atom_ids),
            }
            for q, r in zip(history["training"], training_rows, strict=True)
        ]
        frozen.close()
        reference = invariants(frozen_path)
        branches = {}
        for policy in ("atom_tags_only", "query_evidence"):
            branch_path = output / (sid + "-" + policy + ".sqlite3")
            copy_database(frozen_path, branch_path)
            repository = SQLiteRepository(branch_path)
            try:
                rounds = []
                for round_number in range(1, config["rounds"] + 1):
                    events = []
                    for event in feedback:
                        if not event["selected"]:
                            continue
                        result = LearningService(
                            repository, policy=LEARNING_POLICY_PROFILES[policy]
                        ).apply_feedback(
                            FeedbackRequest(
                                stable_id(
                                    "locomo-feedback",
                                    sid,
                                    policy,
                                    str(round_number),
                                    event["query_id"],
                                ),
                                event["retrieval_id"],
                                tuple(event["selected"]),
                                "positive",
                                reason="official training evidence; exact sources only",
                            )
                        )
                        events.append(asdict(result))
                    rows = evaluate(repository, history["evaluation"])
                    rounds.append(
                        {
                            "round": round_number,
                            "feedback": events,
                            "rows": rows,
                            "metrics": aggregate(rows),
                        }
                    )
                    print(
                        json.dumps(
                            {
                                "history": sid,
                                "policy": policy,
                                "round": round_number,
                                "metrics": aggregate(rows),
                            }
                        ),
                        flush=True,
                    )
                audit_result = WeightLedgerService(repository).audit_namespace(namespace)
                vector_after = evaluate(repository, history["evaluation"], True)
                branches[policy] = {
                    "rounds": rounds,
                    "immutable_state_unchanged": invariants(branch_path) == reference,
                    "weight_ledger_passed": audit_result.passed,
                    "vector_rankings_unchanged": [r["retrieved_atom_ids"] for r in vector_baseline]
                    == [r["retrieved_atom_ids"] for r in vector_after],
                }
            finally:
                repository.close()
        frozen = SQLiteRepository(frozen_path)
        try:
            frozen_after = evaluate(frozen, history["evaluation"])
        finally:
            frozen.close()
        result = {
            "sample_id": sid,
            "development": sid in config["development"],
            "baseline": baseline,
            "vector_baseline": vector_baseline,
            "training_rows": training_rows,
            "fixed_feedback": feedback,
            "branches": branches,
            "frozen_rankings_unchanged": [r["retrieved_atom_ids"] for r in baseline]
            == [r["retrieved_atom_ids"] for r in frozen_after],
        }
        write_json(output / (sid + ".json"), result)
        results.append(result)
    evaluation = [r for r in results if not r["development"]]
    summary = {
        "frozen": aggregate([row for r in evaluation for row in r["baseline"]]),
        "vector_only": aggregate([row for r in evaluation for row in r["vector_baseline"]]),
    }
    for policy in ("atom_tags_only", "query_evidence"):
        summary[policy] = aggregate(
            [row for r in evaluation for row in r["branches"][policy]["rounds"][-1]["rows"]]
        )
    passed = (
        summary["query_evidence"]["disjoint"]["recall"] > summary["frozen"]["disjoint"]["recall"]
        and summary["query_evidence"]["disjoint"]["mrr"] >= summary["frozen"]["disjoint"]["mrr"]
        and all(
            aggregate(r["branches"]["query_evidence"]["rounds"][-1]["rows"])["disjoint"]["recall"]
            >= aggregate(r["baseline"])["disjoint"]["recall"]
            for r in evaluation
        )
    )
    report = {
        "manifest_hash": digest(ROOT / "manifest.json"),
        "runner_hash": digest(Path(__file__)),
        "scoring_seconds": time.perf_counter() - started,
        "primary_gate_passed": passed,
        "summary": summary,
        "histories": results,
    }
    write_json(output / "report.json", report)
    print(
        json.dumps(
            {
                "report": str(output / "report.json"),
                "primary_gate_passed": passed,
                "summary": summary,
            }
        ),
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("stage", choices=("audit", "prepare", "mem0", "features", "score"))
    args = parser.parse_args()
    config = json.loads(CONFIG.read_text())
    data, manifest = audit(config)
    if args.stage == "prepare":
        prepare(config, data, manifest)
    elif args.stage == "mem0":
        mem0(config, manifest)
    elif args.stage == "features":
        features(config, manifest)
    elif args.stage == "score":
        score(config, manifest)


if __name__ == "__main__":
    main()

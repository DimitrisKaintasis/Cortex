from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from data_retrieval.benchmarks.longmemeval import (
    ImportedLongMemEvalCase,
    LongMemEvalIngestService,
)
from data_retrieval.benchmarks.longmemeval_pipeline import LongMemEvalPipelineRunner
from data_retrieval.core.identifiers import content_hash
from data_retrieval.retrieval.embedding import Embedder
from data_retrieval.retrieval.models import QueryPlan, RetrievalChannels
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.repository import Repository
from data_retrieval.tagging.proposals import TagProposer

QUERY_FEATURE_SCHEMA_VERSION = 1


@dataclass(frozen=True, slots=True)
class RetrievalProfile:
    name: str
    description: str
    channels: RetrievalChannels


DEFAULT_RETRIEVAL_PROFILES = (
    RetrievalProfile(
        "lexical_only",
        "keyword overlap only",
        RetrievalChannels(
            tags=False,
            lexical=True,
            semantic=False,
            relationships=False,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "vector_only",
        "Harrier semantic similarity only",
        RetrievalChannels(
            tags=False,
            lexical=False,
            semantic=True,
            relationships=False,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "tags_only",
        "direct canonical tag matches only",
        RetrievalChannels(
            tags=True,
            lexical=False,
            semantic=False,
            relationships=False,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "lexical_vector",
        "conventional lexical and vector hybrid",
        RetrievalChannels(
            tags=False,
            lexical=True,
            semantic=True,
            relationships=False,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "hybrid_direct_tags",
        "lexical and vector retrieval plus direct tag matches",
        RetrievalChannels(
            tags=True,
            lexical=True,
            semantic=True,
            relationships=False,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "hybrid_tag_graph",
        "hybrid retrieval plus weighted relationship expansion",
        RetrievalChannels(
            tags=True,
            lexical=True,
            semantic=True,
            relationships=True,
            temporal=False,
            temporal_summaries=False,
        ),
    ),
    RetrievalProfile(
        "full",
        "all retrieval channels including Temporal summaries and scoring",
        RetrievalChannels(),
    ),
)


@dataclass(frozen=True, slots=True)
class FrozenQueryFeatures:
    question_id: str
    namespace: str
    question_hash: str
    query_tags: tuple[str, ...]
    query_vector: tuple[float, ...]
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "question_id": self.question_id,
            "namespace": self.namespace,
            "question_hash": self.question_hash,
            "query_tags": list(self.query_tags),
            "query_vector": list(self.query_vector),
            "warnings": list(self.warnings),
        }


class LongMemEvalQueryFeatureCache:
    """Create and validate one reusable query representation per benchmark case."""

    def __init__(
        self,
        repository: Repository,
        *,
        tag_proposer: TagProposer,
        embedder: Embedder,
    ) -> None:
        self.repository = repository
        self.tag_proposer = tag_proposer
        self.embedder = embedder

    def load_or_build(
        self,
        *,
        path: Path,
        dataset_id: str,
        dataset_hash: str,
        cases: tuple[ImportedLongMemEvalCase, ...],
    ) -> tuple[dict[str, FrozenQueryFeatures], bool]:
        expected_identity = self._identity(dataset_id, dataset_hash)
        if path.is_file():
            loaded = self._load(path, expected_identity, cases)
            if loaded is not None:
                return loaded, True

        service = RetrievalService(
            self.repository,
            tag_proposer=self.tag_proposer,
            embedder=self.embedder,
        )
        features: dict[str, FrozenQueryFeatures] = {}
        for case in cases:
            plan = QueryPlan(
                query=case.question,
                namespace=case.namespace,
                timeline_id=case.question_id,
                reference_time=case.question_date,
            )
            query_tags, warnings = service.prepare_query_tags(plan)
            features[case.question_id] = FrozenQueryFeatures(
                question_id=case.question_id,
                namespace=case.namespace,
                question_hash=content_hash(case.question),
                query_tags=query_tags,
                query_vector=self.embedder.embed_query(case.question),
                warnings=warnings,
            )

        payload = {
            **expected_identity,
            "cases": [features[case.question_id].as_dict() for case in cases],
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(f"{path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, indent=2), encoding="utf-8", newline="\n"
        )
        temporary.replace(path)
        return features, False

    def _identity(self, dataset_id: str, dataset_hash: str) -> dict[str, Any]:
        return {
            "schema_version": QUERY_FEATURE_SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "dataset_hash": dataset_hash,
            "tag_evidence_source": self.tag_proposer.evidence_source,
            "tag_proposal_version": self.tag_proposer.proposal_version,
            "embedding_provider": self.embedder.provider,
            "embedding_model": self.embedder.model,
        }

    @staticmethod
    def _load(
        path: Path,
        expected_identity: dict[str, Any],
        cases: tuple[ImportedLongMemEvalCase, ...],
    ) -> dict[str, FrozenQueryFeatures] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(payload, dict) or any(
                payload.get(key) != value for key, value in expected_identity.items()
            ):
                return None
            raw_cases = payload.get("cases")
            if not isinstance(raw_cases, list):
                return None
            by_id = {case.question_id: case for case in cases}
            if len(raw_cases) != len(by_id):
                return None
            features: dict[str, FrozenQueryFeatures] = {}
            for raw in raw_cases:
                if not isinstance(raw, dict):
                    return None
                question_id = raw.get("question_id")
                case = by_id.get(question_id)
                if case is None:
                    return None
                feature = FrozenQueryFeatures(
                    question_id=question_id,
                    namespace=str(raw["namespace"]),
                    question_hash=str(raw["question_hash"]),
                    query_tags=tuple(str(tag) for tag in raw["query_tags"]),
                    query_vector=tuple(float(value) for value in raw["query_vector"]),
                    warnings=tuple(str(value) for value in raw.get("warnings", ())),
                )
                if (
                    feature.namespace != case.namespace
                    or feature.question_hash != content_hash(case.question)
                    or not feature.query_vector
                ):
                    return None
                features[question_id] = feature
            return features if set(features) == set(by_id) else None
        except (KeyError, TypeError, ValueError, json.JSONDecodeError):
            return None


class LongMemEvalAblationSuite:
    """Run controlled channel ablations over one persisted LongMemEval corpus."""

    def __init__(
        self,
        repository: Repository,
        *,
        tag_proposer: TagProposer,
        embedder: Embedder,
    ) -> None:
        self.repository = repository
        self.tag_proposer = tag_proposer
        self.embedder = embedder

    def run(
        self,
        *,
        dataset_path: Path,
        dataset_id: str,
        query_feature_path: Path,
        namespace_prefix: str = "longmemeval",
        timezone_name: str = "UTC",
        max_cases: int | None = None,
        question_ids: tuple[str, ...] | None = None,
        top_ks: tuple[int, ...] = (3, 5, 10),
        profiles: tuple[RetrievalProfile, ...] = DEFAULT_RETRIEVAL_PROFILES,
        max_workers: int = 1,
    ) -> dict[str, Any]:
        if not top_ks or any(value <= 0 for value in top_ks):
            raise ValueError("top_ks must contain positive values")
        if len(set(top_ks)) != len(top_ks):
            raise ValueError("top_ks cannot contain duplicates")
        if not profiles or len({profile.name for profile in profiles}) != len(profiles):
            raise ValueError("profiles must have unique names")

        imported = LongMemEvalIngestService(self.repository).ingest_path(
            path=dataset_path,
            namespace_prefix=namespace_prefix,
            dataset_id=dataset_id,
            timezone_name=timezone_name,
            max_cases=max_cases,
            question_ids=question_ids,
        )
        features, reused = LongMemEvalQueryFeatureCache(
            self.repository,
            tag_proposer=self.tag_proposer,
            embedder=self.embedder,
        ).load_or_build(
            path=query_feature_path,
            dataset_id=imported.dataset_id,
            dataset_hash=imported.dataset_hash,
            cases=imported.cases,
        )
        query_tags = {
            question_id: feature.query_tags for question_id, feature in features.items()
        }
        query_vectors = {
            question_id: feature.query_vector for question_id, feature in features.items()
        }
        pipeline = LongMemEvalPipelineRunner(self.repository, embedder=self.embedder)
        results: list[dict[str, Any]] = []
        selected_ids = tuple(case.question_id for case in imported.cases)
        for profile in profiles:
            for top_k in top_ks:
                report = pipeline.run(
                    dataset_path=dataset_path,
                    dataset_id=dataset_id,
                    namespace_prefix=namespace_prefix,
                    timezone_name=timezone_name,
                    max_cases=max_cases,
                    question_ids=selected_ids,
                    top_k=top_k,
                    max_workers=max_workers,
                    retrieval_channels=profile.channels,
                    query_tags_by_question=query_tags,
                    query_vectors_by_question=query_vectors,
                )
                results.append(
                    {
                        "profile": profile.name,
                        "description": profile.description,
                        "channels": {
                            name: getattr(profile.channels, name)
                            for name in (
                                "tags",
                                "lexical",
                                "semantic",
                                "relationships",
                                "temporal",
                                "temporal_summaries",
                                "lineage",
                            )
                        },
                        **report.as_dict(),
                    }
                )
        return {
            "schema_version": 1,
            "dataset_id": imported.dataset_id,
            "dataset_hash": imported.dataset_hash,
            "case_count": imported.case_count,
            "query_features": {
                "path": str(query_feature_path),
                "reused": reused,
                "tag_evidence_source": self.tag_proposer.evidence_source,
                "tag_proposal_version": self.tag_proposer.proposal_version,
                "embedding_provider": self.embedder.provider,
                "embedding_model": self.embedder.model,
            },
            "top_ks": list(top_ks),
            "profiles": [profile.name for profile in profiles],
            "results": results,
        }

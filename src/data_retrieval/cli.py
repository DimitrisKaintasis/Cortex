from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import psycopg

from data_retrieval.benchmarks.capability_suite import IsolatedCapabilitySuite
from data_retrieval.benchmarks.collective_transfer import CollectiveTransferSuite
from data_retrieval.benchmarks.longmemeval import LongMemEvalIngestService
from data_retrieval.benchmarks.longmemeval_ablation import LongMemEvalAblationSuite
from data_retrieval.benchmarks.longmemeval_pipeline import LongMemEvalPipelineRunner
from data_retrieval.benchmarks.mem0_cold_start import Mem0ColdStartSuite
from data_retrieval.benchmarks.mem0_entity_quality import Mem0EntityQualitySuite
from data_retrieval.benchmarks.mem0_experience import Mem0ExperienceSuite
from data_retrieval.benchmarks.review_cascade import ReviewCascadeSuite
from data_retrieval.collective import ShadowRepositoryEvidenceAdapter
from data_retrieval.core.identifiers import stable_id
from data_retrieval.domain.models import TagCandidateState
from data_retrieval.evaluation import EvaluationRunner
from data_retrieval.inference.openrouter import OpenRouterError
from data_retrieval.mem0 import (
    Mem0BootstrapService,
    Mem0ImportService,
    Mem0PythonProcessor,
    Mem0VectorAdmissionPolicy,
    Mem0VectorCalibrationService,
    load_mem0_records,
)
from data_retrieval.retrieval.models import FeedbackRequest, QueryPlan, TemporalMode
from data_retrieval.retrieval.ollama import EMBEDDING_PROFILES, OllamaEmbedder
from data_retrieval.services.calibration_backfill import CalibrationBackfillService
from data_retrieval.services.document_pipeline import (
    DocumentPipelineReport,
    DocumentPipelineService,
    TemporalPipelineRequest,
)
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.interactions import InteractionService
from data_retrieval.services.large_ingestion import LargeFileIngestService
from data_retrieval.services.learning import LEARNING_POLICY_PROFILES, LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.services.tag_lifecycle import TagLifecycleService
from data_retrieval.services.temporal_enrichment import TemporalEnrichmentService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.postgresql import PostgreSQLRepository
from data_retrieval.storage.repository import Repository
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer
from data_retrieval.tagging.ollama import OllamaError, OllamaTagProposer
from data_retrieval.tagging.openrouter import OpenRouterTagProposer
from data_retrieval.temporal import TemporalBridge
from data_retrieval.temporal.ollama import OllamaTemporalSummarizer
from data_retrieval.temporal.openrouter import OpenRouterTemporalSummarizer


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="data-retrieval")
    commands = parser.add_subparsers(dest="command", required=True)

    ingest = commands.add_parser("ingest", help="ingest one UTF-8 text file")
    ingest.add_argument("path", type=Path)
    _add_storage_options(ingest)
    ingest.add_argument("--batch-size", type=int, default=1_000)
    ingest.add_argument("--namespace", required=True)
    ingest.add_argument("--source", help="stable source label; defaults to the input path")
    ingest.add_argument("--tag", action="append", default=[], dest="tags")
    ingest.add_argument("--occurred-at", type=_aware_datetime)
    ingest.add_argument("--timeline-id")
    ingest.add_argument(
        "--metadata-json",
        type=_json_object,
        default={},
        help="additional source metadata as one JSON object",
    )

    process_file = commands.add_parser(
        "process-file",
        help="run canonical ingestion and configured enrichment stages as one checkpointed job",
    )
    process_file.add_argument("path", type=Path)
    _add_storage_options(process_file)
    process_file.add_argument("--namespace", required=True)
    process_file.add_argument("--source", help="stable source label; defaults to the input path")
    process_file.add_argument("--tag", action="append", default=[], dest="tags")
    process_file.add_argument("--occurred-at", type=_aware_datetime)
    process_file.add_argument("--timeline-id")
    process_file.add_argument("--range-start", type=_aware_datetime)
    process_file.add_argument("--range-end", type=_aware_datetime)
    process_file.add_argument("--timezone", default="UTC", dest="timezone_name")
    process_file.add_argument("--temporal-state", type=Path)
    process_file.add_argument("--max-workers", type=int, default=1)
    process_file.add_argument("--batch-size", type=int, default=1_000)
    process_file.add_argument(
        "--metadata-json",
        type=_json_object,
        default={},
        help="additional source metadata as one JSON object",
    )
    process_file.add_argument(
        "--inference-provider",
        choices=("ollama", "openrouter"),
        default="ollama",
        help="provider for optional tag and Temporal model stages",
    )
    process_file.add_argument("--tag-model")
    process_file.add_argument("--temporal-model")
    process_file.add_argument(
        "--embedding-model", default=os.getenv("OLLAMA_EMBEDDING_MODEL")
    )
    process_file.add_argument(
        "--embedding-profile",
        choices=tuple(EMBEDDING_PROFILES),
        default=os.getenv("OLLAMA_EMBEDDING_PROFILE", "symmetric"),
    )
    process_file.add_argument(
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    )
    process_file.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "240")),
    )
    process_file.add_argument(
        "--openrouter-url",
        default=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )
    process_file.add_argument(
        "--openrouter-timeout",
        type=float,
        default=float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "180")),
    )
    process_file.add_argument(
        "--enable-mem0",
        action="store_true",
        help="run Mem0 bootstrap; its dependencies and provider config must be installed",
    )
    process_file.add_argument(
        "--mem0-config",
        type=Path,
        help="Mem0 OSS JSON config; supplying it also enables the Mem0 stage",
    )
    process_file.add_argument("--mem0-user-id")
    process_file.add_argument("--mem0-atom-batch-size", type=int, default=32)
    process_file.add_argument("--mem0-max-batch-chars", type=int, default=24_000)
    process_file.add_argument("--mem0-accept-empty", action="store_true")
    process_file.add_argument("--reject-below-similarity", type=float, default=0.60)
    process_file.add_argument("--provisional-above-similarity", type=float, default=0.80)
    process_file.add_argument("--provisional-weight-cap", type=float, default=0.25)
    process_file.add_argument(
        "--report",
        type=Path,
        help="checkpoint report path; defaults to data/runs/<stable-run-id>.json",
    )

    serve_api = commands.add_parser(
        "serve-api",
        help="serve the local-only ingestion, retrieval, review, and feedback API",
    )
    _add_storage_options(serve_api)
    serve_api.add_argument("--port", type=int, default=8765)
    serve_api.add_argument(
        "--tag-model",
        default=os.getenv("OLLAMA_TAG_MODEL"),
        help="optional Ollama model for query tag generation",
    )
    serve_api.add_argument(
        "--embedding-model", default=os.getenv("OLLAMA_EMBEDDING_MODEL")
    )
    serve_api.add_argument(
        "--embedding-profile",
        choices=tuple(EMBEDDING_PROFILES),
        default=os.getenv("OLLAMA_EMBEDDING_PROFILE", "symmetric"),
    )
    serve_api.add_argument(
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    )
    serve_api.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")),
    )
    serve_api.add_argument(
        "--log-level",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        default="info",
    )

    longmemeval = commands.add_parser(
        "ingest-longmemeval",
        help="stream an official LongMemEval JSON dataset into isolated question namespaces",
    )
    longmemeval.add_argument("path", type=Path)
    _add_storage_options(longmemeval)
    longmemeval.add_argument("--namespace-prefix", default="longmemeval")
    longmemeval.add_argument("--dataset-id")
    longmemeval.add_argument("--timezone", default="UTC", dest="timezone_name")
    longmemeval.add_argument("--max-cases", type=int)
    longmemeval.add_argument(
        "--question-id",
        action="append",
        dest="question_ids",
        help="ingest only selected question IDs; repeat for multiple cases",
    )

    pipeline = commands.add_parser(
        "run-longmemeval",
        help="run resumable LongMemEval enrichment and evidence-retrieval evaluation",
    )
    pipeline.add_argument("path", type=Path)
    _add_storage_options(pipeline)
    pipeline.add_argument("--dataset-id", required=True)
    pipeline.add_argument("--namespace-prefix", default="longmemeval")
    pipeline.add_argument("--timezone", default="UTC", dest="timezone_name")
    pipeline.add_argument("--max-cases", type=int)
    pipeline.add_argument(
        "--question-id",
        action="append",
        dest="question_ids",
        help="run only selected question IDs; repeat for multiple cases",
    )
    pipeline.add_argument(
        "--max-workers",
        type=int,
        default=1,
        help="bounded number of LongMemEval cases processed concurrently",
    )
    pipeline.add_argument("--top-k", type=int, default=10)
    pipeline.add_argument(
        "--evaluation-only",
        action="store_true",
        help=(
            "reuse persisted enrichments and run retrieval only; configured tag and "
            "embedding models remain available for query understanding"
        ),
    )
    pipeline.add_argument("--skip-tags", action="store_true")
    pipeline.add_argument(
        "--resolve-benchmark-tags",
        action="store_true",
        help=(
            "benchmark only: promote exact proposal families above a confidence threshold; "
            "normal ingestion remains quarantined"
        ),
    )
    pipeline.add_argument(
        "--benchmark-tag-min-confidence",
        type=float,
        default=0.65,
    )
    pipeline.add_argument("--skip-temporal", action="store_true")
    pipeline.add_argument("--skip-embeddings", action="store_true")
    pipeline.add_argument(
        "--inference-provider",
        choices=("ollama", "openrouter"),
        default="ollama",
        help="provider for tag proposals and Temporal summaries; embeddings remain on Ollama",
    )
    pipeline.add_argument("--tag-model")
    pipeline.add_argument("--temporal-model")
    pipeline.add_argument("--embedding-model", default=os.getenv("OLLAMA_EMBEDDING_MODEL"))
    pipeline.add_argument(
        "--embedding-profile",
        choices=tuple(EMBEDDING_PROFILES),
        default=os.getenv("OLLAMA_EMBEDDING_PROFILE", "symmetric"),
    )
    pipeline.add_argument(
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    )
    pipeline.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "240")),
    )
    pipeline.add_argument(
        "--openrouter-url",
        default=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )
    pipeline.add_argument(
        "--openrouter-timeout",
        type=float,
        default=float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "180")),
    )
    pipeline.add_argument("--temporal-state", type=Path)
    pipeline.add_argument("--report", type=Path)

    ablation = commands.add_parser(
        "evaluate-longmemeval-ablation",
        help="compare frozen-query retrieval channels over a persisted LongMemEval corpus",
    )
    ablation.add_argument("path", type=Path)
    _add_storage_options(ablation)
    ablation.add_argument("--dataset-id", required=True)
    ablation.add_argument("--namespace-prefix", default="longmemeval")
    ablation.add_argument("--timezone", default="UTC", dest="timezone_name")
    ablation.add_argument("--max-cases", type=int)
    ablation.add_argument(
        "--question-id",
        action="append",
        dest="question_ids",
        help="evaluate only selected question IDs; repeat for multiple cases",
    )
    ablation.add_argument("--max-workers", type=int, default=1)
    ablation.add_argument("--top-k", type=int, action="append", dest="top_ks")
    ablation.add_argument(
        "--inference-provider",
        choices=("ollama", "openrouter"),
        default="ollama",
    )
    ablation.add_argument("--tag-model")
    ablation.add_argument("--embedding-model", default=os.getenv("OLLAMA_EMBEDDING_MODEL"))
    ablation.add_argument(
        "--embedding-profile",
        choices=tuple(EMBEDDING_PROFILES),
        default=os.getenv("OLLAMA_EMBEDDING_PROFILE", "symmetric"),
    )
    ablation.add_argument(
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435")
    )
    ablation.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "240")),
    )
    ablation.add_argument(
        "--openrouter-url",
        default=os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
    )
    ablation.add_argument(
        "--openrouter-timeout",
        type=float,
        default=float(os.getenv("OPENROUTER_TIMEOUT_SECONDS", "180")),
    )
    ablation.add_argument(
        "--query-features",
        type=Path,
        default=Path("data/results/longmemeval-query-features-v1.json"),
    )
    ablation.add_argument(
        "--report",
        type=Path,
        default=Path("data/results/longmemeval-ablation-v1.json"),
    )

    tags = commands.add_parser(
        "enrich-tags", help="add Ollama tag proposals to an ingested document"
    )
    tags.add_argument("document_id")
    _add_storage_options(tags)
    _add_ollama_options(tags, timeout_default="120")
    tags.add_argument("--embedding-model", default=os.getenv("OLLAMA_EMBEDDING_MODEL"))
    tags.add_argument(
        "--embedding-profile",
        choices=tuple(EMBEDDING_PROFILES),
        default=os.getenv("OLLAMA_EMBEDDING_PROFILE", "symmetric"),
    )

    candidates = commands.add_parser(
        "list-tag-candidates", help="list quarantined or resolved AI tag candidates"
    )
    _add_storage_options(candidates)
    candidates.add_argument("--namespace", required=True)
    candidates.add_argument(
        "--state",
        choices=tuple(state.value for state in TagCandidateState),
        default=TagCandidateState.PROPOSED.value,
    )
    candidates.add_argument("--limit", type=int, default=100)

    resolve_candidate = commands.add_parser(
        "resolve-tag-candidate", help="promote, merge, or reject one tag candidate"
    )
    resolve_candidate.add_argument("candidate_id")
    _add_storage_options(resolve_candidate)
    resolve_candidate.add_argument(
        "--action", choices=("promote", "merge", "reject"), required=True
    )
    resolve_candidate.add_argument(
        "--canonical-tag", help="existing canonical tag required for merge"
    )
    resolve_candidate.add_argument("--reason", help="required reason for rejection")

    temporal = commands.add_parser(
        "enrich-temporal", help="create Temporal History summary atoms from stored atoms"
    )
    _add_storage_options(temporal)
    temporal.add_argument("--namespace", required=True)
    temporal.add_argument("--timeline-id", required=True)
    temporal.add_argument("--range-start", required=True, type=_aware_datetime)
    temporal.add_argument("--range-end", required=True, type=_aware_datetime)
    temporal.add_argument("--timezone", default="UTC", dest="timezone_name")
    temporal.add_argument("--state", type=Path)
    temporal.add_argument("--max-workers", type=int, default=1)
    _add_ollama_options(temporal, timeout_default="240")

    embeddings = commands.add_parser(
        "enrich-embeddings", help="embed all changed atoms in one namespace"
    )
    _add_storage_options(embeddings)
    embeddings.add_argument("--namespace", required=True)
    _add_embedding_options(embeddings)

    mem0 = commands.add_parser(
        "import-mem0",
        help="import a Mem0 JSON/JSONL export as native, boosted calibration evidence",
    )
    mem0.add_argument("path", type=Path)
    _add_storage_options(mem0)
    mem0.add_argument("--namespace", required=True)
    mem0.add_argument(
        "--tag-model",
        default=os.getenv("OLLAMA_MODEL"),
        help="optional Ollama model used to tag records that do not contain tags",
    )
    _add_embedding_options(mem0, required=False)

    mem0_bootstrap = commands.add_parser(
        "bootstrap-mem0",
        help="extract a provenance-linked Mem0 entity graph from existing atoms",
    )
    _add_storage_options(mem0_bootstrap)
    mem0_scope = mem0_bootstrap.add_mutually_exclusive_group(required=True)
    mem0_scope.add_argument("--namespace")
    mem0_scope.add_argument("--namespace-prefix")
    mem0_scope.add_argument("--all-namespaces", action="store_true")
    mem0_bootstrap.add_argument(
        "--mem0-config",
        type=Path,
        help="Mem0 OSS JSON config; provider secrets should come from environment variables",
    )
    mem0_bootstrap.add_argument(
        "--mem0-user-id",
        help="optional Mem0 identity; by default each native namespace is isolated",
    )
    mem0_bootstrap.add_argument("--atom-batch-size", type=int, default=32)
    mem0_bootstrap.add_argument("--max-batch-chars", type=int, default=24_000)
    mem0_bootstrap.add_argument(
        "--accept-empty",
        action="store_true",
        help="mark empty Mem0 results complete; default leaves them retryable",
    )
    mem0_bootstrap.add_argument(
        "--max-documents",
        type=int,
        help="global safety cap across all selected namespaces",
    )
    mem0_vectors = commands.add_parser(
        "calibrate-mem0-vectors",
        help="assign bounded provisional weights to Mem0 entity proposals",
    )
    _add_storage_options(mem0_vectors)
    mem0_vectors.add_argument("--namespace", required=True)
    _add_embedding_options(mem0_vectors)
    mem0_vectors.add_argument("--max-links", type=int)
    mem0_vectors.add_argument("--reject-below-similarity", type=float, default=0.60)
    mem0_vectors.add_argument("--provisional-above-similarity", type=float, default=0.80)
    mem0_vectors.add_argument("--provisional-weight-cap", type=float, default=0.25)
    calibration = commands.add_parser(
        "backfill-calibration",
        help="replay missing teacher priors and initial relationships without re-embedding",
    )
    _add_storage_options(calibration)
    calibration_scope = calibration.add_mutually_exclusive_group(required=True)
    calibration_scope.add_argument("--namespace")
    calibration_scope.add_argument("--namespace-prefix")
    calibration_scope.add_argument("--all-namespaces", action="store_true")
    calibration.add_argument("--document-batch-size", type=int, default=250)
    calibration.add_argument("--atom-batch-size", type=int, default=1_000)
    calibration.add_argument(
        "--max-documents",
        type=int,
        help="global safety cap across the selected namespace scope",
    )

    weight_audit = commands.add_parser(
        "audit-weights", help="reconstruct serving weights from immutable events"
    )
    _add_storage_options(weight_audit)
    weight_audit.add_argument("--namespace", required=True)
    weight_audit.add_argument(
        "--repair-aggregates",
        action="store_true",
        help="replace mismatched serving aggregates with ledger-reconstructed weights",
    )

    interaction = commands.add_parser(
        "record-interaction",
        help="store one user/assistant turn and optionally apply attributable outcome learning",
    )
    _add_storage_options(interaction)
    interaction.add_argument("--namespace", required=True)
    interaction.add_argument("--conversation-id", required=True)
    interaction.add_argument("--turn-id", required=True)
    interaction.add_argument("--user-text", required=True)
    interaction.add_argument("--assistant-text", required=True)
    interaction.add_argument("--retrieval-id")
    interaction.add_argument("--used-atom", action="append", default=[])
    interaction.add_argument("--outcome", choices=("positive", "negative"))
    interaction.add_argument("--reason", default="")
    interaction.add_argument("--used-mem0", action="store_true")
    interaction.add_argument("--tag", action="append", default=[])

    retrieve = commands.add_parser("retrieve", help="run explainable hybrid retrieval")
    retrieve.add_argument("query")
    _add_storage_options(retrieve)
    retrieve.add_argument("--namespace", required=True)
    retrieve.add_argument("--tag", action="append", default=[], dest="tags")
    retrieve.add_argument("--top-k", type=int, default=10)
    retrieve.add_argument("--timeline-id")
    retrieve.add_argument(
        "--temporal-mode",
        choices=tuple(mode.value for mode in TemporalMode),
        default=TemporalMode.AUTO.value,
    )
    retrieve.add_argument("--as-of", type=_aware_datetime)
    retrieve.add_argument("--range-start", type=_aware_datetime)
    retrieve.add_argument("--range-end", type=_aware_datetime)
    retrieve.add_argument("--reference-time", type=_aware_datetime)
    retrieve.add_argument(
        "--tag-model",
        default=os.getenv("OLLAMA_TAG_MODEL"),
        help=(
            "optional Ollama model for generated query tags; retrieval degrades safely without it"
        ),
    )
    _add_embedding_options(retrieve, required=False)

    feedback = commands.add_parser(
        "feedback", help="apply one explicit outcome to a recorded retrieval"
    )
    feedback.add_argument("retrieval_id")
    _add_storage_options(feedback)
    feedback.add_argument("--feedback-id", default=None)
    feedback.add_argument("--selected-atom", action="append", required=True)
    feedback.add_argument("--outcome", choices=("positive", "negative"), required=True)
    feedback.add_argument("--reason", default="")
    feedback.add_argument(
        "--used-mem0",
        action="store_true",
        help="apply the accepted Mem0 2x training-signal multiplier",
    )

    evaluate = commands.add_parser("evaluate", help="run the retrieval evaluation corpus")
    evaluate.add_argument("--dataset", type=Path, default=Path("evals/retrieval_cases.json"))
    _add_storage_options(
        evaluate,
        sqlite_env="DATA_RETRIEVAL_EVAL_DB",
        sqlite_default="evaluation.sqlite3",
    )
    evaluate.add_argument(
        "--tag-model",
        default=os.getenv("OLLAMA_TAG_MODEL"),
        help="optional Ollama model for generated evaluation query tags",
    )
    _add_embedding_options(evaluate, required=False)

    capabilities = commands.add_parser(
        "evaluate-capabilities",
        help="run deterministic isolated architecture contracts",
    )
    capabilities.add_argument(
        "--fixture",
        type=Path,
        default=Path("evals/isolated_capabilities_v1.json"),
    )
    capabilities.add_argument(
        "--report",
        type=Path,
        default=Path("data/results/isolated-capabilities.json"),
    )
    collective = commands.add_parser(
        "evaluate-collective-transfer",
        help="run the deterministic shadow collective-transfer experiment",
    )
    collective.add_argument(
        "--fixture",
        type=Path,
        default=Path("evals/collective_transfer_v1.json"),
    )
    collective.add_argument(
        "--report",
        type=Path,
        default=Path("data/results/collective-transfer-v1.json"),
    )
    cascade = commands.add_parser(
        "evaluate-review-cascade",
        help="compare cheap-only, review-everything, and gated AI review",
    )
    cascade.add_argument(
        "--fixture",
        type=Path,
        default=Path("evals/review_cascade_v1.json"),
    )
    cascade.add_argument(
        "--report",
        type=Path,
        default=Path("data/results/review-cascade-v1.json"),
    )
    mem0_quality = commands.add_parser(
        "evaluate-mem0-entities",
        help="run the live labeled Mem0 entity/provenance quality gate",
    )
    mem0_quality.add_argument(
        "--fixture",
        type=Path,
        default=Path("evals/mem0_entity_quality_v1.json"),
    )
    mem0_quality.add_argument(
        "--mem0-config",
        type=Path,
        default=Path("evals/mem0_entity_smoke_config.json"),
    )
    mem0_quality.add_argument(
        "--case-id",
        action="append",
        dest="case_ids",
        help="run only one labeled case; repeat to select several cases",
    )
    mem0_quality.add_argument(
        "--report",
        type=Path,
        default=Path("data/results/mem0-entity-quality-v1.json"),
    )
    cold_start = commands.add_parser(
        "evaluate-mem0-cold-start",
        help="compare baseline, vector, Mem0, and guarded joint typed-edge profiles",
    )
    cold_start.add_argument(
        "--fixture",
        type=Path,
        default=Path("evals/mem0_entity_quality_v1.json"),
    )
    cold_start.add_argument(
        "--mem0-report",
        type=Path,
        default=Path("data/results/mem0-entity-quality-v1.json"),
    )
    cold_start.add_argument(
        "--report",
        type=Path,
        default=Path("data/results/mem0-cold-start-v1.json"),
    )
    _add_embedding_options(cold_start)
    cold_start.add_argument("--reject-below-similarity", type=float, default=0.60)
    cold_start.add_argument("--provisional-above-similarity", type=float, default=0.80)
    cold_start.add_argument("--provisional-weight-cap", type=float, default=0.25)
    experience = commands.add_parser(
        "evaluate-mem0-experience",
        help="measure a prepared Mem0 entity graph across attributable usage rounds",
    )
    _add_storage_options(experience)
    experience.add_argument(
        "--fixture",
        type=Path,
        default=Path("evals/mem0_experience_v1.json"),
    )
    experience.add_argument(
        "--feedback-selection",
        choices=("first_relevant", "all_relevant"),
        default="first_relevant",
    )
    experience.add_argument(
        "--learning-policy",
        choices=tuple(LEARNING_POLICY_PROFILES),
        default="all_pairs",
        help="versioned feedback channels to evaluate",
    )
    experience.add_argument("--usage-rounds", type=int)
    experience.add_argument("--top-k", type=int)
    _add_embedding_options(experience)
    experience.add_argument(
        "--report",
        type=Path,
        default=Path("data/results/mem0-experience-v1.json"),
    )
    repository_features = commands.add_parser(
        "observe-repository-features",
        help="run payload-free collective triage without feature-path mutations",
    )
    _add_storage_options(repository_features)
    repository_features.add_argument("--namespace", required=True)
    repository_features.add_argument("--embedding-provider")
    repository_features.add_argument("--embedding-model")
    repository_features.add_argument("--limit", type=int)
    repository_features.add_argument(
        "--report",
        type=Path,
        default=Path("data/results/repository-features-v1.json"),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command in {"enrich-tags", "enrich-temporal"} and not args.ollama_model:
        parser.error("--ollama-model or OLLAMA_MODEL is required")
    if args.command == "enrich-embeddings" and not args.embedding_model:
        parser.error("--embedding-model or OLLAMA_EMBEDDING_MODEL is required")

    try:
        if args.command == "ingest":
            output = _ingest(args, parser)
        elif args.command == "process-file":
            output = _process_file(args, parser)
        elif args.command == "serve-api":
            output = _serve_api(args)
        elif args.command == "ingest-longmemeval":
            output = _ingest_longmemeval(args, parser)
        elif args.command == "run-longmemeval":
            output = _run_longmemeval(args, parser)
        elif args.command == "evaluate-longmemeval-ablation":
            output = _evaluate_longmemeval_ablation(args, parser)
        elif args.command == "enrich-tags":
            output = _enrich_tags(args)
        elif args.command == "list-tag-candidates":
            output = _list_tag_candidates(args)
        elif args.command == "resolve-tag-candidate":
            output = _resolve_tag_candidate(args)
        elif args.command == "enrich-temporal":
            output = _enrich_temporal(args)
        elif args.command == "enrich-embeddings":
            output = _enrich_embeddings(args)
        elif args.command == "import-mem0":
            output = _import_mem0(args, parser)
        elif args.command == "bootstrap-mem0":
            output = _bootstrap_mem0(args, parser)
        elif args.command == "calibrate-mem0-vectors":
            output = _calibrate_mem0_vectors(args)
        elif args.command == "backfill-calibration":
            output = _backfill_calibration(args)
        elif args.command == "audit-weights":
            output = _audit_weights(args)
        elif args.command == "record-interaction":
            output = _record_interaction(args)
        elif args.command == "retrieve":
            output = _retrieve(args)
        elif args.command == "feedback":
            output = _feedback(args)
        elif args.command == "evaluate":
            output = _evaluate(args)
        elif args.command == "evaluate-capabilities":
            output = _evaluate_capabilities(args, parser)
        elif args.command == "evaluate-collective-transfer":
            output = _evaluate_collective_transfer(args, parser)
        elif args.command == "evaluate-review-cascade":
            output = _evaluate_review_cascade(args, parser)
        elif args.command == "evaluate-mem0-entities":
            output = _evaluate_mem0_entities(args, parser)
        elif args.command == "evaluate-mem0-cold-start":
            output = _evaluate_mem0_cold_start(args, parser)
        elif args.command == "evaluate-mem0-experience":
            output = _evaluate_mem0_experience(args, parser)
        else:
            output = _observe_repository_features(args, parser)
    except (
        OSError,
        UnicodeError,
        OllamaError,
        OpenRouterError,
        RuntimeError,
        ValueError,
        sqlite3.Error,
        psycopg.Error,
    ) as error:
        print(f"{args.command} failed: {error}", file=sys.stderr)
        return 1

    print(json.dumps(output, indent=2))
    if (
        args.command
        in {
            "evaluate-capabilities",
            "evaluate-collective-transfer",
            "evaluate-review-cascade",
            "evaluate-mem0-entities",
            "evaluate-mem0-cold-start",
            "evaluate-mem0-experience",
        }
        and output["passed"] is False
    ):
        return 1
    return 0


def _ingest(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, object]:
    if not args.path.is_file():
        parser.error(f"input file does not exist: {args.path}")
    metadata = {"source_type": "text_file", **args.metadata_json}
    if args.timeline_id:
        metadata["timeline_id"] = args.timeline_id
    with _open_repository(args) as repository:
        if args.postgres_dsn:
            result = LargeFileIngestService(repository, batch_size=args.batch_size).ingest_path(
                path=args.path,
                namespace=args.namespace,
                source=args.source or args.path.as_posix(),
                explicit_tags=tuple(args.tags),
                occurred_at=args.occurred_at,
                metadata=metadata,
            )
            atom_ids: tuple[str, ...] = ()
            atom_count = result.atom_count
        else:
            text = args.path.read_text(encoding="utf-8")
            result = IngestService(repository).ingest_text(
                namespace=args.namespace,
                source=args.source or args.path.as_posix(),
                text=text,
                explicit_tags=tuple(args.tags),
                occurred_at=args.occurred_at,
                metadata=metadata,
            )
            atom_ids = result.atom_ids
            atom_count = len(atom_ids)
    return {
        "document_id": result.document_id,
        "atom_ids": atom_ids,
        "atom_count": atom_count,
        "tag_ids": result.tag_ids,
        "idempotent": result.idempotent,
        "calibration_signals": getattr(result, "calibration_signals", None),
        "database": _database_label(args),
    }


def _process_file(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.path.is_file():
        parser.error(f"input file does not exist: {args.path}")
    if args.mem0_config and not args.mem0_config.is_file():
        parser.error(f"Mem0 config does not exist: {args.mem0_config}")
    temporal_values = (args.timeline_id, args.range_start, args.range_end)
    if any(value is not None for value in temporal_values) and not all(
        value is not None for value in temporal_values
    ):
        parser.error(
            "--timeline-id, --range-start, and --range-end must be supplied together"
        )
    if args.temporal_model and not all(value is not None for value in temporal_values):
        parser.error("--temporal-model requires a timeline and temporal range")
    if all(value is not None for value in temporal_values) and not args.temporal_model:
        parser.error("a temporal range requires --temporal-model")
    if args.range_start and args.range_end and args.range_start >= args.range_end:
        parser.error("--range-start must be earlier than --range-end")

    needs_inference = bool(args.tag_model or args.temporal_model)
    api_key = (
        _openrouter_api_key(parser)
        if args.inference_provider == "openrouter" and needs_inference
        else None
    )
    if args.tag_model and args.inference_provider == "openrouter":
        assert api_key is not None
        tag_proposer = OpenRouterTagProposer(
            api_key=api_key,
            model=args.tag_model,
            base_url=args.openrouter_url,
            timeout_seconds=args.openrouter_timeout,
        )
    elif args.tag_model:
        tag_proposer = OllamaTagProposer(
            base_url=args.ollama_url,
            model=args.tag_model,
            timeout_seconds=args.ollama_timeout,
        )
    else:
        tag_proposer = None

    if args.temporal_model and args.inference_provider == "openrouter":
        assert api_key is not None
        temporal_bridge = TemporalBridge(
            OpenRouterTemporalSummarizer(
                api_key=api_key,
                model=args.temporal_model,
                base_url=args.openrouter_url,
                timeout_seconds=args.openrouter_timeout,
            )
        )
    elif args.temporal_model:
        temporal_bridge = TemporalBridge(
            OllamaTemporalSummarizer(
                base_url=args.ollama_url,
                model=args.temporal_model,
                timeout_seconds=args.ollama_timeout,
            )
        )
    else:
        temporal_bridge = None

    embedder = _embedder(args) if args.embedding_model else None
    mem0_enabled = args.enable_mem0 or args.mem0_config is not None
    mem0_config = _load_json_object(args.mem0_config) if args.mem0_config else None
    mem0_processor = Mem0PythonProcessor(mem0_config) if mem0_enabled else None
    temporal_request = None
    if args.temporal_model:
        assert args.timeline_id and args.range_start and args.range_end
        temporal_request = TemporalPipelineRequest(
            timeline_id=args.timeline_id,
            timezone_name=args.timezone_name,
            range_start=args.range_start,
            range_end=args.range_end,
            state_path=(
                args.temporal_state
                or Path("data/state")
                / f"{stable_id('temporal-state', args.namespace)}.sqlite3"
            ),
            max_workers=args.max_workers,
        )

    metadata = {"source_type": "text_file", **args.metadata_json}
    if args.timeline_id:
        metadata["timeline_id"] = args.timeline_id
    report_path: Path | None = args.report

    def checkpoint(report: DocumentPipelineReport) -> None:
        nonlocal report_path
        report_path = report_path or Path("data/runs") / f"{report.run_id}.json"
        _write_json_atomic(report_path, report.as_dict())

    with _open_repository(args) as repository:
        report = DocumentPipelineService(
            repository,
            tag_proposer=tag_proposer,
            embedder=embedder,
            temporal_bridge=temporal_bridge,
            mem0_processor=mem0_processor,
            mem0_admission_policy=Mem0VectorAdmissionPolicy(
                reject_below_similarity=args.reject_below_similarity,
                provisional_above_similarity=args.provisional_above_similarity,
                provisional_weight_cap=args.provisional_weight_cap,
            ),
            ingest_batch_size=args.batch_size,
            mem0_atom_batch_size=args.mem0_atom_batch_size,
            mem0_max_batch_chars=args.mem0_max_batch_chars,
            mem0_accept_empty=args.mem0_accept_empty,
        ).run(
            path=args.path,
            namespace=args.namespace,
            source=args.source or args.path.as_posix(),
            explicit_tags=tuple(args.tags),
            occurred_at=args.occurred_at,
            metadata=metadata,
            temporal=temporal_request,
            mem0_user_id=args.mem0_user_id,
            checkpoint=checkpoint,
        )
    assert report_path is not None
    return {
        **report.as_dict(),
        "report": str(report_path),
        "database": _database_label(args),
    }


def _serve_api(args: argparse.Namespace) -> dict[str, object]:
    if not 1 <= args.port <= 65_535:
        raise ValueError("port must be between 1 and 65535")
    try:
        import uvicorn

        from data_retrieval.api import LocalApiConfig, create_app
    except ImportError as error:
        raise ValueError(
            "API dependencies are not installed; install them with "
            "'python -m pip install -e .[api]'"
        ) from error

    host = "127.0.0.1"
    print(
        f"Data Retrieval local API: http://{host}:{args.port}/docs",
        file=sys.stderr,
        flush=True,
    )
    uvicorn.run(
        create_app(
            LocalApiConfig(
                database_path=args.db,
                postgres_dsn=args.postgres_dsn,
                ollama_url=args.ollama_url,
                ollama_timeout_seconds=args.ollama_timeout,
                tag_model=args.tag_model,
                embedding_model=args.embedding_model,
                embedding_profile=args.embedding_profile,
            )
        ),
        host=host,
        port=args.port,
        log_level=args.log_level,
    )
    return {
        "status": "stopped",
        "host": host,
        "port": args.port,
        "database": _database_label(args),
    }


def _ingest_longmemeval(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.path.is_file():
        parser.error(f"input file does not exist: {args.path}")
    with _open_repository(args) as repository:
        result = LongMemEvalIngestService(repository).ingest_path(
            path=args.path,
            namespace_prefix=args.namespace_prefix,
            dataset_id=args.dataset_id,
            timezone_name=args.timezone_name,
            max_cases=args.max_cases,
            question_ids=tuple(args.question_ids) if args.question_ids else None,
        )
    return {
        "dataset_id": result.dataset_id,
        "dataset_hash": result.dataset_hash,
        "case_count": result.case_count,
        "session_count": result.session_count,
        "inserted_session_count": result.inserted_session_count,
        "reused_session_count": result.reused_session_count,
        "atom_count": result.atom_count,
        "namespace_prefix": args.namespace_prefix,
        "database": _database_label(args),
    }


def _run_longmemeval(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.path.is_file():
        parser.error(f"input file does not exist: {args.path}")
    tag_model = _inference_model(args.inference_provider, args.tag_model)
    temporal_model = _inference_model(args.inference_provider, args.temporal_model)
    if not args.skip_tags and not tag_model:
        parser.error("--tag-model or the selected provider's model environment is required")
    if not args.evaluation_only and not args.skip_temporal and not temporal_model:
        parser.error("--temporal-model or the selected provider's model environment is required")
    if not args.skip_embeddings and not args.embedding_model:
        parser.error(
            "--embedding-model or OLLAMA_EMBEDDING_MODEL is required unless "
            "--skip-embeddings is used"
        )

    api_key = (
        _openrouter_api_key(parser)
        if args.inference_provider == "openrouter"
        and (not args.skip_tags or (not args.evaluation_only and not args.skip_temporal))
        else None
    )
    if args.skip_tags:
        tag_proposer = None
    elif args.inference_provider == "openrouter":
        assert api_key is not None and tag_model is not None
        tag_proposer = OpenRouterTagProposer(
            api_key=api_key,
            model=tag_model,
            base_url=args.openrouter_url,
            timeout_seconds=args.openrouter_timeout,
        )
    else:
        assert tag_model is not None
        tag_proposer = OllamaTagProposer(
            base_url=args.ollama_url,
            model=tag_model,
            timeout_seconds=args.ollama_timeout,
        )
    embedder = None if args.skip_embeddings else _embedder(args)
    if args.skip_temporal or args.evaluation_only:
        temporal_bridge = None
    elif args.inference_provider == "openrouter":
        assert api_key is not None and temporal_model is not None
        temporal_bridge = TemporalBridge(
            OpenRouterTemporalSummarizer(
                api_key=api_key,
                model=temporal_model,
                base_url=args.openrouter_url,
                timeout_seconds=args.openrouter_timeout,
            )
        )
    else:
        assert temporal_model is not None
        temporal_bridge = TemporalBridge(
            OllamaTemporalSummarizer(
                base_url=args.ollama_url,
                model=temporal_model,
                timeout_seconds=args.ollama_timeout,
            )
        )
    temporal_state = args.temporal_state or Path(f"data/state/{args.dataset_id}-temporal.sqlite3")
    report_path = args.report or Path(f"data/results/{args.dataset_id}-pipeline-report.json")

    def progress(index: int, total: int, stage: str) -> None:
        if stage == "enriching" and (index == 1 or index % 10 == 0 or index == total):
            print(f"LongMemEval case {index}/{total}", file=sys.stderr, flush=True)

    with _open_repository(args) as repository:
        report = LongMemEvalPipelineRunner(
            repository,
            tag_proposer=tag_proposer,
            embedder=embedder,
            temporal_bridge=temporal_bridge,
        ).run(
            dataset_path=args.path,
            dataset_id=args.dataset_id,
            temporal_state_path=temporal_state,
            namespace_prefix=args.namespace_prefix,
            timezone_name=args.timezone_name,
            max_cases=args.max_cases,
            question_ids=tuple(args.question_ids) if args.question_ids else None,
            top_k=args.top_k,
            enrich_tags=not args.skip_tags and not args.evaluation_only,
            enrich_temporal=not args.skip_temporal and not args.evaluation_only,
            enrich_embeddings=not args.skip_embeddings and not args.evaluation_only,
            resolve_benchmark_tags=(args.resolve_benchmark_tags and not args.evaluation_only),
            benchmark_tag_min_confidence=args.benchmark_tag_min_confidence,
            max_workers=args.max_workers,
            progress=progress,
        )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8", newline="\n")
    return {
        **report.as_dict(include_cases=False),
        "inference_provider": args.inference_provider,
        "tag_model": tag_model if not args.skip_tags else None,
        "temporal_model": (
            temporal_model if not args.skip_temporal and not args.evaluation_only else None
        ),
        "embedding_model": embedder.model if embedder else None,
        "report": str(report_path),
        "database": _database_label(args),
    }


def _evaluate_longmemeval_ablation(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.path.is_file():
        parser.error(f"input file does not exist: {args.path}")
    tag_model = _inference_model(args.inference_provider, args.tag_model)
    if not tag_model:
        parser.error("--tag-model or the selected provider's model environment is required")
    if not args.embedding_model:
        parser.error("--embedding-model or OLLAMA_EMBEDDING_MODEL is required")

    if args.inference_provider == "openrouter":
        tag_proposer = OpenRouterTagProposer(
            api_key=_openrouter_api_key(parser),
            model=tag_model,
            base_url=args.openrouter_url,
            timeout_seconds=args.openrouter_timeout,
        )
    else:
        tag_proposer = OllamaTagProposer(
            base_url=args.ollama_url,
            model=tag_model,
            timeout_seconds=args.ollama_timeout,
        )
    embedder = _embedder(args)
    with _open_repository(args) as repository:
        report = LongMemEvalAblationSuite(
            repository,
            tag_proposer=tag_proposer,
            embedder=embedder,
        ).run(
            dataset_path=args.path,
            dataset_id=args.dataset_id,
            query_feature_path=args.query_features,
            namespace_prefix=args.namespace_prefix,
            timezone_name=args.timezone_name,
            max_cases=args.max_cases,
            question_ids=tuple(args.question_ids) if args.question_ids else None,
            top_ks=tuple(args.top_ks or (3, 5, 10)),
            max_workers=args.max_workers,
        )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(json.dumps(report, indent=2), encoding="utf-8", newline="\n")
    return {
        "dataset_id": report["dataset_id"],
        "case_count": report["case_count"],
        "profiles": report["profiles"],
        "top_ks": report["top_ks"],
        "query_features_reused": report["query_features"]["reused"],
        "query_features": str(args.query_features),
        "report": str(args.report),
        "database": _database_label(args),
    }


def _enrich_tags(args: argparse.Namespace) -> dict[str, object]:
    proposer = OllamaTagProposer(
        base_url=args.ollama_url,
        model=args.ollama_model,
        timeout_seconds=args.ollama_timeout,
    )
    with _open_repository(args) as repository:
        canonicalizer = SemanticTagCanonicalizer(_embedder(args)) if args.embedding_model else None
        result = TagEnrichmentService(
            repository, proposer, canonicalizer=canonicalizer
        ).enrich_document(args.document_id)
    return {
        "document_id": result.document_id,
        "tag_ids": result.tag_ids,
        "candidate_ids": result.candidate_ids,
        "atom_tag_count": result.atom_tag_count,
        "proposed_count": result.proposed_count,
        "resolved_count": result.resolved_count,
        "idempotent": result.idempotent,
        "database": _database_label(args),
    }


def _list_tag_candidates(args: argparse.Namespace) -> dict[str, object]:
    with _open_repository(args) as repository:
        candidates = repository.list_tag_candidates(
            namespace=args.namespace,
            state=TagCandidateState(args.state),
            limit=args.limit,
        )
    return {
        "namespace": args.namespace,
        "state": args.state,
        "count": len(candidates),
        "candidates": [
            {
                "candidate_id": candidate.candidate_id,
                "atom_id": candidate.atom_id,
                "text": candidate.normalized_text,
                "display_text": candidate.display_text,
                "level": candidate.level.value,
                "confidence": candidate.confidence,
                "producer": candidate.producer,
                "proposal_version": candidate.proposal_version,
                "resolved_tag_id": candidate.resolved_tag_id,
                "resolution_reason": candidate.resolution_reason,
                "created_at": candidate.created_at.isoformat(),
                "resolved_at": (
                    candidate.resolved_at.isoformat() if candidate.resolved_at else None
                ),
            }
            for candidate in candidates
        ],
        "database": _database_label(args),
    }


def _resolve_tag_candidate(args: argparse.Namespace) -> dict[str, object]:
    with _open_repository(args) as repository:
        service = TagLifecycleService(repository)
        if args.action == "promote":
            if args.canonical_tag or args.reason:
                raise ValueError("promote does not accept --canonical-tag or --reason")
            result = service.promote(args.candidate_id)
        elif args.action == "merge":
            if not args.canonical_tag:
                raise ValueError("merge requires --canonical-tag")
            if args.reason:
                raise ValueError("merge does not accept --reason")
            result = service.merge(args.candidate_id, args.canonical_tag)
        else:
            if not args.reason:
                raise ValueError("reject requires --reason")
            if args.canonical_tag:
                raise ValueError("reject does not accept --canonical-tag")
            result = service.reject(args.candidate_id, reason=args.reason)
    return {
        "candidate_id": result.candidate.candidate_id,
        "state": result.candidate.state.value,
        "resolved_tag_id": result.candidate.resolved_tag_id,
        "canonical_tag": result.tag.canonical_text if result.tag else None,
        "atom_id": result.candidate.atom_id,
        "atom_tag_activated": result.atom_tag is not None,
        "resolution_reason": result.candidate.resolution_reason,
        "database": _database_label(args),
    }


def _enrich_temporal(args: argparse.Namespace) -> dict[str, object]:
    state_path = args.state or (
        Path("temporal-state.sqlite3")
        if args.postgres_dsn
        else args.db.with_name(f"{args.db.stem}.temporal-state.sqlite3")
    )
    summarizer = OllamaTemporalSummarizer(
        base_url=args.ollama_url,
        model=args.ollama_model,
        timeout_seconds=args.ollama_timeout,
    )
    with _open_repository(args) as repository:
        result = TemporalEnrichmentService(repository, TemporalBridge(summarizer)).enrich_range(
            namespace=args.namespace,
            timeline_id=args.timeline_id,
            timezone_name=args.timezone_name,
            range_start=args.range_start,
            range_end=args.range_end,
            state_path=state_path,
            max_workers=args.max_workers,
        )
    return {
        "document_id": result.bundle.document.document_id,
        "summary_counts": result.summary_counts,
        "coverage_count": result.coverage_count,
        "generation": result.generation,
        "database": _database_label(args),
        "temporal_state": str(state_path),
    }


def _enrich_embeddings(args: argparse.Namespace) -> dict[str, object]:
    embedder = _embedder(args)
    with _open_repository(args) as repository:
        result = EmbeddingEnrichmentService(repository, embedder).enrich_namespace(args.namespace)
    return {
        "namespace": result.namespace,
        "embedded_atom_ids": result.embedded_atom_ids,
        "reused_atom_ids": result.reused_atom_ids,
        "embedded_count": result.embedded_count,
        "reused_count": result.reused_count,
        "reported_ids_truncated": result.reported_ids_truncated,
        "provider": embedder.provider,
        "model": embedder.model,
        "database": _database_label(args),
    }


def _import_mem0(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, object]:
    if not args.path.is_file():
        parser.error(f"input file does not exist: {args.path}")
    records = load_mem0_records(args.path)
    embedder = _embedder(args) if args.embedding_model else None
    tag_proposer = (
        OllamaTagProposer(
            base_url=args.ollama_url,
            model=args.tag_model,
            timeout_seconds=args.ollama_timeout,
        )
        if args.tag_model
        else None
    )
    imported = 0
    exact_duplicates = 0
    semantic_duplicates = 0
    conflict_links = 0
    source_lineage_links = 0
    calibration_signals = 0
    calibration_warnings: set[str] = set()
    with _open_repository(args) as repository:
        service = Mem0ImportService(
            repository,
            embedder=embedder,
            tag_proposer=tag_proposer,
        )
        for offset in range(0, len(records), 500):
            result = service.import_records(
                namespace=args.namespace,
                records=records[offset : offset + 500],
            )
            imported += len(result.imported_record_ids)
            exact_duplicates += len(result.exact_duplicate_record_ids)
            semantic_duplicates += len(result.semantic_duplicate_record_ids)
            conflict_links += result.conflict_links_created
            source_lineage_links += result.source_lineage_links_created
            calibration_signals += result.calibration_signals_created
            calibration_warnings.update(result.calibration_warnings)
    return {
        "record_count": len(records),
        "imported": imported,
        "exact_duplicates": exact_duplicates,
        "semantic_duplicates": semantic_duplicates,
        "conflict_links_created": conflict_links,
        "source_lineage_links_created": source_lineage_links,
        "calibration_signals_created": calibration_signals,
        "calibration_warnings": sorted(calibration_warnings),
        "database": _database_label(args),
    }


def _bootstrap_mem0(args: argparse.Namespace, parser: argparse.ArgumentParser) -> dict[str, object]:
    if args.mem0_user_id and not args.namespace:
        parser.error("--mem0-user-id requires one exact --namespace")
    if args.mem0_config and not args.mem0_config.is_file():
        parser.error(f"Mem0 config does not exist: {args.mem0_config}")
    config = _load_json_object(args.mem0_config) if args.mem0_config else None
    processor = Mem0PythonProcessor(config)
    with _open_repository(args) as repository:
        namespaces = (
            (args.namespace,)
            if args.namespace
            else repository.list_namespaces(prefix=args.namespace_prefix)
        )
        results = []
        remaining = args.max_documents
        for index, namespace in enumerate(namespaces, start=1):
            if remaining is not None and remaining <= 0:
                break
            print(
                f"Mem0 bootstrap namespace {index}/{len(namespaces)}: {namespace}",
                file=sys.stderr,
                flush=True,
            )
            result = Mem0BootstrapService(
                repository,
                processor,
                atom_batch_size=args.atom_batch_size,
                max_batch_chars=args.max_batch_chars,
                accept_empty=args.accept_empty,
            ).run(
                namespace=namespace,
                user_id=args.mem0_user_id,
                max_documents=remaining,
            )
            results.append(result)
            if remaining is not None:
                remaining -= result.documents_examined
    return {
        "namespace_count": len(results),
        "documents_examined": sum(result.documents_examined for result in results),
        "batches_processed": sum(result.batches_processed for result in results),
        "batches_resumed": sum(result.batches_resumed for result in results),
        "source_atoms_processed": sum(result.source_atoms_processed for result in results),
        "source_atoms_resumed": sum(result.source_atoms_resumed for result in results),
        "mem0_records_returned": sum(result.mem0_records_returned for result in results),
        "entities_returned": sum(result.entities_returned for result in results),
        "relationships_returned": sum(result.relationships_returned for result in results),
        "relationships_quarantined": sum(result.relationships_quarantined for result in results),
        "empty_batches": sum(result.empty_batches for result in results),
        "entities_imported": sum(result.entities_imported for result in results),
        "entity_support_links_created": sum(
            result.entity_support_links_created for result in results
        ),
        "entity_relationship_links_created": sum(
            result.entity_relationship_links_created for result in results
        ),
        "calibration_signals_created": sum(
            result.calibration_signals_created for result in results
        ),
        "warnings": sorted({warning for result in results for warning in result.warnings}),
        "scope_truncated": len(results) < len(namespaces)
        or any(result.truncated for result in results),
        "database": _database_label(args),
    }


def _calibrate_mem0_vectors(args: argparse.Namespace) -> dict[str, object]:
    embedder = _embedder(args)
    policy = Mem0VectorAdmissionPolicy(
        reject_below_similarity=args.reject_below_similarity,
        provisional_above_similarity=args.provisional_above_similarity,
        provisional_weight_cap=args.provisional_weight_cap,
    )
    with _open_repository(args) as repository:
        result = Mem0VectorCalibrationService(
            repository,
            embedder,
            policy=policy,
        ).calibrate_namespace(args.namespace, max_links=args.max_links)
    return {**result.as_dict(), "database": _database_label(args)}


def _backfill_calibration(args: argparse.Namespace) -> dict[str, object]:
    with _open_repository(args) as repository:
        service = CalibrationBackfillService(
            repository,
            document_batch_size=args.document_batch_size,
            atom_batch_size=args.atom_batch_size,
        )
        namespaces = (
            (args.namespace,)
            if args.namespace
            else repository.list_namespaces(prefix=args.namespace_prefix)
        )
        collected = []
        remaining = args.max_documents
        for namespace in namespaces:
            if remaining is not None and remaining <= 0:
                break
            result = service.run(namespace=namespace, max_documents=remaining)
            collected.append(result)
            if remaining is not None:
                remaining -= result.documents_examined
        results = tuple(collected)
    return {
        "namespace_count": len(results),
        "documents_examined": sum(result.documents_examined for result in results),
        "documents_changed": sum(result.documents_changed for result in results),
        "signals_created": sum(result.signals_created for result in results),
        "atom_tag_updates": sum(result.atom_tag_updates for result in results),
        "atom_link_updates": sum(result.atom_link_updates for result in results),
        "tag_relation_updates": sum(result.tag_relation_updates for result in results),
        "truncated_namespaces": tuple(result.namespace for result in results if result.truncated),
        "scope_truncated": len(results) < len(namespaces)
        or any(result.truncated for result in results),
        "database": _database_label(args),
    }


def _audit_weights(args: argparse.Namespace) -> dict[str, object]:
    with _open_repository(args) as repository:
        service = WeightLedgerService(repository)
        audit = (
            service.repair_aggregates(args.namespace)
            if args.repair_aggregates
            else service.audit_namespace(args.namespace)
        )
    issues = [
        {
            "target_type": target.target_type.value,
            "target_id": target.target_id,
            "related_id": target.related_id,
            "relation_type": target.relation_type,
            "event_count": target.event_count,
            "reconstructed_weight": target.reconstructed_weight,
            "aggregate_weight": target.aggregate_weight,
            "issues": target.issues,
        }
        for target in audit.targets
        if not target.valid
    ]
    return {
        "namespace": audit.namespace,
        "passed": audit.passed,
        "repair_requested": args.repair_aggregates,
        "target_count": audit.target_count,
        "event_count": audit.event_count,
        "valid_target_count": audit.valid_target_count,
        "mismatched_target_count": audit.mismatched_target_count,
        "missing_event_target_count": audit.missing_event_target_count,
        "issues": issues[:100],
        "issues_truncated": len(issues) > 100,
        "database": _database_label(args),
    }


def _record_interaction(args: argparse.Namespace) -> dict[str, object]:
    with _open_repository(args) as repository:
        result = InteractionService(repository).record_turn(
            namespace=args.namespace,
            conversation_id=args.conversation_id,
            turn_id=args.turn_id,
            user_text=args.user_text,
            assistant_text=args.assistant_text,
            retrieval_id=args.retrieval_id,
            used_atom_ids=tuple(args.used_atom),
            outcome=args.outcome,
            reason=args.reason,
            used_mem0=args.used_mem0,
            tags=tuple(args.tag),
        )
    return {
        "conversation_id": result.conversation_id,
        "turn_id": result.turn_id,
        "user_atom_ids": result.user_atom_ids,
        "assistant_atom_ids": result.assistant_atom_ids,
        "evidence_link_count": result.evidence_link_count,
        "feedback_id": result.feedback.feedback_id if result.feedback else None,
        "database": _database_label(args),
    }


def _retrieve(args: argparse.Namespace) -> dict[str, object]:
    embedder = _embedder(args) if args.embedding_model else None
    tag_proposer = (
        OllamaTagProposer(
            base_url=args.ollama_url,
            model=args.tag_model,
            timeout_seconds=args.ollama_timeout,
        )
        if args.tag_model
        else None
    )
    plan = QueryPlan(
        query=args.query,
        namespace=args.namespace,
        query_tags=tuple(args.tags),
        top_k=args.top_k,
        timeline_id=args.timeline_id,
        temporal_mode=TemporalMode(args.temporal_mode),
        as_of=args.as_of,
        range_start=args.range_start,
        range_end=args.range_end,
        reference_time=args.reference_time,
    )
    with _open_repository(args) as repository:
        result = RetrievalService(
            repository,
            tag_proposer=tag_proposer,
            embedder=embedder,
        ).retrieve(plan)
    return {
        "retrieval_id": result.retrieval_id,
        "resolved_temporal_mode": result.resolved_temporal_mode.value,
        "low_confidence": result.low_confidence,
        "query_tag_model": args.tag_model,
        "diagnostics": result.diagnostics,
        "items": [
            {
                "atom_id": item.atom_id,
                "content": item.content,
                "kind": item.kind.value,
                "occurred_at": item.occurred_at.isoformat() if item.occurred_at else None,
                "role": item.role,
                "atom_role": item.atom_role.value,
                "temporal_label": item.temporal_label.value,
                "lineage_atom_ids": item.lineage_atom_ids,
                "score": {
                    "tag": item.score.tag,
                    "lexical": item.score.lexical,
                    "semantic": item.score.semantic,
                    "relationship": item.score.relationship,
                    "temporal": item.score.temporal,
                    "final": item.score.final,
                    "evidence": item.score.evidence,
                },
            }
            for item in result.items
        ],
    }


def _feedback(args: argparse.Namespace) -> dict[str, object]:
    feedback_id = args.feedback_id or str(uuid4())
    with _open_repository(args) as repository:
        result = LearningService(repository).apply_feedback(
            FeedbackRequest(
                feedback_id=feedback_id,
                retrieval_id=args.retrieval_id,
                selected_atom_ids=tuple(args.selected_atom),
                outcome=args.outcome,
                reason=args.reason,
                used_mem0=args.used_mem0,
            )
        )
    return {
        "feedback_id": result.feedback_id,
        "credited_atom_ids": result.credited_atom_ids,
        "atom_tag_updates": result.atom_tag_updates,
        "atom_link_updates": result.atom_link_updates,
        "tag_relation_updates": result.tag_relation_updates,
        "learning_multiplier": result.learning_multiplier,
        "database": _database_label(args),
    }


def _evaluate(args: argparse.Namespace) -> dict[str, object]:
    embedder = _embedder(args) if args.embedding_model else None
    tag_proposer = (
        OllamaTagProposer(
            base_url=args.ollama_url,
            model=args.tag_model,
            timeout_seconds=args.ollama_timeout,
        )
        if args.tag_model
        else None
    )
    with _open_repository(args) as repository:
        report = EvaluationRunner(
            repository,
            tag_proposer=tag_proposer,
            embedder=embedder,
        ).run(args.dataset)
    return {
        **report.as_dict(),
        "dataset": str(args.dataset),
        "database": _database_label(args),
        "query_tag_model": args.tag_model,
        "embedding_model": embedder.model if embedder else None,
    }


def _evaluate_capabilities(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.fixture.is_file():
        parser.error(f"capability fixture does not exist: {args.fixture}")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = IsolatedCapabilitySuite().run(
        args.fixture,
        artifact_location=args.report,
    )
    payload = report.as_dict()
    args.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _evaluate_collective_transfer(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.fixture.is_file():
        parser.error(f"collective transfer fixture does not exist: {args.fixture}")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = CollectiveTransferSuite().run(
        args.fixture,
        artifact_location=args.report,
    )
    payload = report.as_dict()
    args.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _evaluate_review_cascade(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.fixture.is_file():
        parser.error(f"review cascade fixture does not exist: {args.fixture}")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = ReviewCascadeSuite().run(
        args.fixture,
        artifact_location=args.report,
    )
    payload = report.as_dict()
    args.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _evaluate_mem0_entities(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.fixture.is_file():
        parser.error(f"Mem0 entity quality fixture does not exist: {args.fixture}")
    if not args.mem0_config.is_file():
        parser.error(f"Mem0 config does not exist: {args.mem0_config}")
    config = _load_json_object(args.mem0_config)
    processor = Mem0PythonProcessor(config)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    report = Mem0EntityQualitySuite().run(
        args.fixture,
        processor,
        case_ids=tuple(args.case_ids) if args.case_ids else None,
    )
    payload = report.as_dict()
    args.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _evaluate_mem0_cold_start(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.fixture.is_file():
        parser.error(f"Mem0 entity quality fixture does not exist: {args.fixture}")
    if not args.mem0_report.is_file():
        parser.error(f"Mem0 entity quality report does not exist: {args.mem0_report}")
    policy = Mem0VectorAdmissionPolicy(
        reject_below_similarity=args.reject_below_similarity,
        provisional_above_similarity=args.provisional_above_similarity,
        provisional_weight_cap=args.provisional_weight_cap,
    )
    report = Mem0ColdStartSuite().run(
        fixture_path=args.fixture,
        mem0_report_path=args.mem0_report,
        embedder=_embedder(args),
        policy=policy,
    )
    payload = report.as_dict()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _evaluate_mem0_experience(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if not args.fixture.is_file():
        parser.error(f"Mem0 experience fixture does not exist: {args.fixture}")
    fixture = _load_json_object(args.fixture)
    try:
        dataset_path = Path(str(fixture["dataset_path"]))
        dataset_id = str(fixture["dataset_id"])
        namespace_prefix = str(fixture["namespace_prefix"])
        query_feature_path = Path(str(fixture["query_feature_path"]))
        holdout_query_path = (
            Path(str(fixture["holdout_query_path"]))
            if fixture.get("holdout_query_path")
            else None
        )
        holdout_query_feature_path = (
            Path(str(fixture["holdout_query_feature_path"]))
            if fixture.get("holdout_query_feature_path")
            else None
        )
        raw_question_ids = fixture["question_ids"]
        if not isinstance(raw_question_ids, list):
            raise TypeError("question_ids must be an array")
        question_ids = tuple(str(value) for value in raw_question_ids)
        suite_id = str(fixture["suite_id"])
        usage_rounds = (
            args.usage_rounds
            if args.usage_rounds is not None
            else int(fixture.get("usage_rounds", 5))
        )
        top_k = args.top_k if args.top_k is not None else int(fixture.get("top_k", 10))
    except (KeyError, TypeError, ValueError) as error:
        parser.error(f"invalid Mem0 experience fixture: {error}")
    if not dataset_path.is_file():
        parser.error(f"LongMemEval dataset does not exist: {dataset_path}")
    if not query_feature_path.is_file():
        parser.error(f"query feature cache does not exist: {query_feature_path}")
    if holdout_query_path is not None and not holdout_query_path.is_file():
        parser.error(f"holdout query fixture does not exist: {holdout_query_path}")
    with _open_repository(args) as repository:
        report = Mem0ExperienceSuite(repository, embedder=_embedder(args)).run(
            suite_id=suite_id,
            dataset_path=dataset_path,
            dataset_id=dataset_id,
            query_feature_path=query_feature_path,
            namespace_prefix=namespace_prefix,
            question_ids=question_ids,
            feedback_selection=args.feedback_selection,
            learning_policy=LEARNING_POLICY_PROFILES[args.learning_policy],
            holdout_query_path=holdout_query_path,
            holdout_query_feature_path=holdout_query_feature_path,
            usage_round_count=usage_rounds,
            top_k=top_k,
        )
    payload = report.as_dict()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return {
        "suite_id": report.suite_id,
        "feedback_selection": report.feedback_selection,
        "learning_policy": report.learning_policy,
        "usage_round_count": report.usage_round_count,
        "mechanical_passed": report.mechanical_passed,
        "learning_signal_passed": report.learning_signal_passed,
        "holdout_final_passed": report.holdout_final_passed,
        "holdout_regression_free": report.holdout_regression_free,
        "metric_deltas": report.metric_deltas,
        "holdout_metric_deltas": report.holdout_metric_deltas,
        "report": str(args.report),
        "database": _database_label(args),
        "passed": report.mechanical_passed,
    }


def _observe_repository_features(
    args: argparse.Namespace, parser: argparse.ArgumentParser
) -> dict[str, object]:
    if (args.embedding_provider is None) != (args.embedding_model is None):
        parser.error("--embedding-provider and --embedding-model must be supplied together")
    if args.limit is not None and args.limit <= 0:
        parser.error("--limit must be positive")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    with _open_repository(args) as repository:
        report = ShadowRepositoryEvidenceAdapter(repository).observe_namespace(
            namespace=args.namespace,
            embedding_provider=args.embedding_provider,
            embedding_model=args.embedding_model,
            limit=args.limit,
        )
    payload = {
        **report.as_dict(),
        "database": _database_label(args),
        "artifact_location": str(args.report),
    }
    args.report.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return payload


def _add_ollama_options(parser: argparse.ArgumentParser, *, timeout_default: str) -> None:
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435"),
    )
    parser.add_argument("--ollama-model", default=os.getenv("OLLAMA_MODEL"))
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", timeout_default)),
    )


def _add_storage_options(
    parser: argparse.ArgumentParser,
    *,
    sqlite_env: str = "DATA_RETRIEVAL_DB",
    sqlite_default: str = "data.sqlite3",
) -> None:
    parser.add_argument("--db", type=Path, default=_env_path(sqlite_env, sqlite_default))
    parser.add_argument(
        "--postgres-dsn",
        default=os.getenv("DATA_RETRIEVAL_POSTGRES_DSN"),
        help="PostgreSQL connection string; takes precedence over --db",
    )


def _open_repository(args: argparse.Namespace) -> Repository:
    if args.postgres_dsn:
        return PostgreSQLRepository(args.postgres_dsn)
    return SQLiteRepository(args.db)


def _database_label(args: argparse.Namespace) -> str:
    return "postgresql" if args.postgres_dsn else str(args.db)


def _add_embedding_options(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11435"),
    )
    parser.add_argument(
        "--embedding-model",
        default=os.getenv("OLLAMA_EMBEDDING_MODEL"),
        required=required and not os.getenv("OLLAMA_EMBEDDING_MODEL"),
    )
    parser.add_argument(
        "--embedding-profile",
        choices=tuple(EMBEDDING_PROFILES),
        default=os.getenv("OLLAMA_EMBEDDING_PROFILE", "symmetric"),
    )
    parser.add_argument(
        "--ollama-timeout",
        type=float,
        default=float(os.getenv("OLLAMA_TIMEOUT_SECONDS", "120")),
    )


def _embedder(args: argparse.Namespace) -> OllamaEmbedder:
    return OllamaEmbedder(
        base_url=args.ollama_url,
        model_name=args.embedding_model,
        timeout_seconds=args.ollama_timeout,
        profile_name=args.embedding_profile,
    )


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


def _inference_model(provider: str, explicit_model: str | None) -> str | None:
    if explicit_model:
        return explicit_model
    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", "openai/gpt-5.6-luna")
    return os.getenv("OLLAMA_MODEL")


def _openrouter_api_key(parser: argparse.ArgumentParser) -> str:
    api_key = os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        parser.error("OPENROUTER_API_KEY is required for --inference-provider openrouter")
    return api_key


def _aware_datetime(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as error:
        raise argparse.ArgumentTypeError("expected an ISO-8601 datetime") from error
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise argparse.ArgumentTypeError("datetime must include a UTC offset")
    return parsed


def _json_object(value: str) -> dict[str, object]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as error:
        raise argparse.ArgumentTypeError("expected a JSON object") from error
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("metadata must be a JSON object")
    return parsed


def _load_json_object(path: Path) -> dict[str, object]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise ValueError(f"Mem0 config is not valid JSON: {path}") from error
    if not isinstance(payload, dict):
        raise ValueError("Mem0 config must contain one JSON object")
    return payload


def _write_json_atomic(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    temporary.replace(path)


if __name__ == "__main__":
    raise SystemExit(main())

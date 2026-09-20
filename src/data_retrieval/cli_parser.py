from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path

from data_retrieval.domain.models import TagCandidateState
from data_retrieval.retrieval.models import TemporalMode
from data_retrieval.retrieval.ollama import EMBEDDING_PROFILES
from data_retrieval.services.learning import LEARNING_POLICY_PROFILES


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
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
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
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
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

    serve_mcp = commands.add_parser(
        "serve-mcp",
        help="serve local-only Cortex query and outcome tools over stdio MCP",
    )
    _add_storage_options(serve_mcp)

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
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
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
        "--ollama-url", default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434")
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

    postgres_backup = commands.add_parser(
        "postgres-backup",
        help="create a checksummed logical backup of laptop PostgreSQL",
    )
    postgres_backup.add_argument("--output", type=Path)
    postgres_backup.add_argument(
        "--compose-file", type=Path, default=Path("compose.postgres.yml")
    )
    postgres_backup.add_argument("--overwrite", action="store_true")

    postgres_verify = commands.add_parser(
        "postgres-verify-backup",
        help="restore a PostgreSQL backup into an isolated temporary database",
    )
    postgres_verify.add_argument("path", type=Path)
    postgres_verify.add_argument(
        "--compose-file", type=Path, default=Path("compose.postgres.yml")
    )
    return parser


def _add_ollama_options(parser: argparse.ArgumentParser, *, timeout_default: str) -> None:
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
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


def _add_embedding_options(parser: argparse.ArgumentParser, *, required: bool = True) -> None:
    parser.add_argument(
        "--ollama-url",
        default=os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434"),
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


def _env_path(name: str, default: str) -> Path:
    return Path(os.getenv(name, default))


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

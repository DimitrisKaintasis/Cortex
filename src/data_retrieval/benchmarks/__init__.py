"""Benchmark helpers, loaded lazily so the core CLI has no benchmark dependencies."""

from importlib import import_module
from typing import Any

_EXPORT_MODULES = {
    "CapabilityGateReport": "capability_suite",
    "GateCheck": "capability_suite",
    "IsolatedCapabilitySuite": "capability_suite",
    "IsolatedCapabilitySuiteReport": "capability_suite",
    "CollectiveTransferReport": "collective_transfer",
    "CollectiveTransferSuite": "collective_transfer",
    "ExperimentCheck": "collective_transfer",
    "PolicyVariantResult": "collective_transfer",
    "LongMemEvalCase": "longmemeval",
    "LongMemEvalImportResult": "longmemeval",
    "LongMemEvalIngestService": "longmemeval",
    "iter_longmemeval_cases": "longmemeval",
    "DEFAULT_RETRIEVAL_PROFILES": "longmemeval_ablation",
    "FrozenQueryFeatures": "longmemeval_ablation",
    "LongMemEvalAblationSuite": "longmemeval_ablation",
    "LongMemEvalQueryFeatureCache": "longmemeval_ablation",
    "RetrievalProfile": "longmemeval_ablation",
    "ColdStartProfileResult": "mem0_cold_start",
    "ColdStartProposalResult": "mem0_cold_start",
    "Mem0ColdStartReport": "mem0_cold_start",
    "Mem0ColdStartSuite": "mem0_cold_start",
    "ExpectedRelationship": "mem0_entity_quality",
    "Mem0EntityQualityCase": "mem0_entity_quality",
    "Mem0EntityQualityReport": "mem0_entity_quality",
    "Mem0EntityQualitySuite": "mem0_entity_quality",
    "Mem0EntityQualityThresholds": "mem0_entity_quality",
    "QualityAtom": "mem0_entity_quality",
    "load_mem0_entity_quality_fixture": "mem0_entity_quality",
    "score_mem0_entity_quality": "mem0_entity_quality",
    "EXPERIENCE_PROFILES": "mem0_experience",
    "ExperienceReport": "mem0_experience",
    "ExperienceRound": "mem0_experience",
    "Mem0ExperienceSuite": "mem0_experience",
    "CascadeScenarioResult": "review_cascade",
    "ReviewCascadeReport": "review_cascade",
    "ReviewCascadeSuite": "review_cascade",
    "BenchmarkTagCatalogResolver": "tag_catalog",
    "BenchmarkTagResolutionResult": "tag_catalog",
}

__all__ = list(_EXPORT_MODULES)


def __getattr__(name: str) -> Any:
    module_name = _EXPORT_MODULES.get(name)
    if module_name is None:
        raise AttributeError(name)
    module = import_module(f"data_retrieval.benchmarks.{module_name}")
    value = getattr(module, name)
    globals()[name] = value
    return value

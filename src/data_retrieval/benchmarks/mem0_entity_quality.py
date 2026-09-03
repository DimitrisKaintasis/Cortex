from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from data_retrieval.core.identifiers import stable_id
from data_retrieval.mem0.bootstrap import Mem0Processor
from data_retrieval.mem0.entities import Mem0ProcessResult


@dataclass(frozen=True, slots=True)
class QualityAtom:
    source_id: str
    content: str
    role: str = "user"


@dataclass(frozen=True, slots=True)
class ExpectedRelationship:
    source: str
    target: str
    accepted_predicates: tuple[str, ...]
    evidence_source_ids: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Mem0EntityQualityCase:
    case_id: str
    atoms: tuple[QualityAtom, ...]
    expected_entities: tuple[str, ...]
    expected_relationships: tuple[ExpectedRelationship, ...]


@dataclass(frozen=True, slots=True)
class Mem0EntityQualityThresholds:
    entity_precision: float = 0.9
    entity_recall: float = 0.8
    relationship_endpoint_precision: float = 0.9
    relationship_endpoint_recall: float = 0.7
    relationship_direction_accuracy: float = 0.9
    predicate_fidelity: float = 0.7
    evidence_precision: float = 0.9
    evidence_recall: float = 0.8
    maximum_quarantine_rate: float = 0.15
    maximum_empty_output_rate: float = 0.1


@dataclass(frozen=True, slots=True)
class Mem0EntityCaseResult:
    case_id: str
    duration_ms: float
    expected_entities: tuple[str, ...]
    actual_entities: tuple[str, ...]
    expected_relationships: tuple[dict[str, Any], ...]
    actual_relationships: tuple[dict[str, Any], ...]
    relationships_quarantined: int
    warnings: tuple[str, ...]
    error: str | None


@dataclass(frozen=True, slots=True)
class Mem0EntityQualityReport:
    suite_id: str
    evaluation_id: str
    processor_profile: str
    fixture_path: str
    passed: bool
    duration_ms: float
    metrics: dict[str, float | int]
    thresholds: dict[str, float]
    failed_thresholds: tuple[str, ...]
    cases: tuple[Mem0EntityCaseResult, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "evaluation_id": self.evaluation_id,
            "processor_profile": self.processor_profile,
            "fixture_path": self.fixture_path,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "metrics": self.metrics,
            "thresholds": self.thresholds,
            "failed_thresholds": list(self.failed_thresholds),
            "cases": [asdict(case) for case in self.cases],
        }


@dataclass(slots=True)
class _Counts:
    expected_entities: int = 0
    actual_entities: int = 0
    entity_matches: int = 0
    expected_relationships: int = 0
    actual_relationships: int = 0
    directed_matches: int = 0
    reversed_matches: int = 0
    predicate_matches: int = 0
    expected_evidence: int = 0
    actual_evidence: int = 0
    evidence_matches: int = 0
    evidence_relationships_scored: int = 0
    quarantined: int = 0
    expected_nonempty_cases: int = 0
    empty_output_cases: int = 0
    errors: int = 0


class Mem0EntityQualitySuite:
    """Run a small labeled extraction gate without involving serving retrieval."""

    def run(
        self,
        fixture_path: Path,
        processor: Mem0Processor,
        *,
        case_ids: tuple[str, ...] | None = None,
    ) -> Mem0EntityQualityReport:
        suite_id, thresholds, cases = load_mem0_entity_quality_fixture(fixture_path)
        if case_ids:
            requested = set(case_ids)
            known = {case.case_id for case in cases}
            unknown = sorted(requested - known)
            if unknown:
                raise ValueError("unknown Mem0 entity quality case IDs: " + ", ".join(unknown))
            cases = tuple(case for case in cases if case.case_id in requested)
        evaluation_id = uuid4().hex
        profile = str(getattr(processor, "profile_id", "unknown"))
        started = time.perf_counter()
        results: list[Mem0EntityCaseResult] = []
        for case in cases:
            case_started = time.perf_counter()
            error: str | None = None
            try:
                output = processor.add(
                    tuple({"role": atom.role, "content": atom.content} for atom in case.atoms),
                    user_id=f"mem0-quality:{evaluation_id}:{case.case_id}",
                    run_id=evaluation_id,
                    metadata={
                        "source_system": "mem0-entity-quality",
                        "source_atom_ids": [atom.source_id for atom in case.atoms],
                        "bootstrap_batch_id": stable_id(
                            "mem0-quality", evaluation_id, case.case_id
                        ),
                    },
                )
            except Exception as exc:  # provider failures belong in the quality report
                output = Mem0ProcessResult()
                error = f"{type(exc).__name__}: {exc}"
            results.append(
                _case_result(
                    case,
                    output,
                    duration_ms=(time.perf_counter() - case_started) * 1_000,
                    error=error,
                )
            )
        metrics = _score(cases, tuple(results))
        threshold_values = asdict(thresholds)
        failed = _failed_thresholds(metrics, thresholds)
        return Mem0EntityQualityReport(
            suite_id=suite_id,
            evaluation_id=evaluation_id,
            processor_profile=profile,
            fixture_path=str(fixture_path),
            passed=not failed and metrics["error_count"] == 0,
            duration_ms=(time.perf_counter() - started) * 1_000,
            metrics=metrics,
            thresholds=threshold_values,
            failed_thresholds=failed,
            cases=tuple(results),
        )


def load_mem0_entity_quality_fixture(
    path: Path,
) -> tuple[str, Mem0EntityQualityThresholds, tuple[Mem0EntityQualityCase, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("Mem0 entity quality fixture must be one JSON object")
    suite_id = _required_text(payload, "suite_id")
    raw_thresholds = payload.get("thresholds", {})
    if not isinstance(raw_thresholds, dict):
        raise ValueError("thresholds must be one JSON object")
    thresholds = Mem0EntityQualityThresholds(**raw_thresholds)
    for name, value in asdict(thresholds).items():
        if not 0.0 <= value <= 1.0:
            raise ValueError(f"threshold {name} must be between 0 and 1")
    raw_cases = payload.get("cases")
    if not isinstance(raw_cases, list) or not raw_cases:
        raise ValueError("cases must be a non-empty JSON array")
    cases = tuple(_load_case(item) for item in raw_cases)
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("case_id values must be unique")
    return suite_id, thresholds, cases


def score_mem0_entity_quality(
    *,
    suite_id: str,
    cases: tuple[Mem0EntityQualityCase, ...],
    outputs: tuple[Mem0ProcessResult, ...],
    thresholds: Mem0EntityQualityThresholds | None = None,
) -> Mem0EntityQualityReport:
    """Score supplied outputs; intended for deterministic tests and saved-provider replays."""

    if len(cases) != len(outputs):
        raise ValueError("quality cases and outputs must have equal lengths")
    results = tuple(
        _case_result(case, output, duration_ms=0.0, error=None)
        for case, output in zip(cases, outputs, strict=True)
    )
    selected_thresholds = thresholds or Mem0EntityQualityThresholds()
    metrics = _score(cases, results)
    failed = _failed_thresholds(metrics, selected_thresholds)
    return Mem0EntityQualityReport(
        suite_id=suite_id,
        evaluation_id="deterministic",
        processor_profile="supplied-output",
        fixture_path="",
        passed=not failed,
        duration_ms=0.0,
        metrics=metrics,
        thresholds=asdict(selected_thresholds),
        failed_thresholds=failed,
        cases=results,
    )


def _load_case(value: Any) -> Mem0EntityQualityCase:
    if not isinstance(value, dict):
        raise ValueError("each quality case must be one JSON object")
    case_id = _required_text(value, "case_id")
    raw_atoms = value.get("atoms")
    if not isinstance(raw_atoms, list) or not raw_atoms:
        raise ValueError(f"case {case_id} atoms must be a non-empty JSON array")
    atoms = tuple(
        QualityAtom(
            source_id=_required_text(item, "source_id"),
            content=_required_text(item, "content"),
            role=str(item.get("role", "user")),
        )
        for item in raw_atoms
        if isinstance(item, dict)
    )
    if len(atoms) != len(raw_atoms) or len({atom.source_id for atom in atoms}) != len(atoms):
        raise ValueError(f"case {case_id} atom source IDs must be unique objects")
    allowed_ids = {atom.source_id for atom in atoms}
    raw_entities = value.get("expected_entities", [])
    if not isinstance(raw_entities, list):
        raise ValueError(f"case {case_id} expected_entities must be a JSON array")
    raw_relationships = value.get("expected_relationships", [])
    if not isinstance(raw_relationships, list):
        raise ValueError(f"case {case_id} expected_relationships must be a JSON array")
    relationships = tuple(
        _load_relationship(case_id, item, allowed_ids) for item in raw_relationships
    )
    return Mem0EntityQualityCase(
        case_id=case_id,
        atoms=atoms,
        expected_entities=tuple(str(item) for item in raw_entities),
        expected_relationships=relationships,
    )


def _load_relationship(
    case_id: str, value: Any, allowed_ids: set[str]
) -> ExpectedRelationship:
    if not isinstance(value, dict):
        raise ValueError(f"case {case_id} relationships must be JSON objects")
    predicates = value.get("accepted_predicates")
    evidence = value.get("evidence_source_ids")
    if not isinstance(predicates, list) or not predicates:
        raise ValueError(f"case {case_id} accepted_predicates must be a non-empty array")
    if not isinstance(evidence, list) or not evidence:
        raise ValueError(f"case {case_id} evidence_source_ids must be a non-empty array")
    evidence_ids = tuple(str(item) for item in evidence)
    if not set(evidence_ids).issubset(allowed_ids):
        raise ValueError(f"case {case_id} relationship uses an unknown evidence source ID")
    return ExpectedRelationship(
        source=_required_text(value, "source"),
        target=_required_text(value, "target"),
        accepted_predicates=tuple(str(item) for item in predicates),
        evidence_source_ids=evidence_ids,
    )


def _required_text(value: dict[str, Any], field: str) -> str:
    result = value.get(field)
    if not isinstance(result, str) or not result.strip():
        raise ValueError(f"{field} must be a non-empty string")
    return result.strip()


def _case_result(
    case: Mem0EntityQualityCase,
    output: Mem0ProcessResult,
    *,
    duration_ms: float,
    error: str | None,
) -> Mem0EntityCaseResult:
    return Mem0EntityCaseResult(
        case_id=case.case_id,
        duration_ms=round(duration_ms, 3),
        expected_entities=case.expected_entities,
        actual_entities=tuple(entity.name for entity in output.entities),
        expected_relationships=tuple(asdict(item) for item in case.expected_relationships),
        actual_relationships=tuple(
            {
                "source": _entity_name(output, item.source_entity_id),
                "target": _entity_name(output, item.target_entity_id),
                "predicate": item.predicate,
                "evidence_source_ids": item.support_atom_ids,
            }
            for item in output.relationships
        ),
        relationships_quarantined=output.relationships_quarantined,
        warnings=output.warnings,
        error=error,
    )


def _entity_name(output: Mem0ProcessResult, entity_id: str) -> str:
    names = {entity.entity_id: entity.name for entity in output.entities}
    return names.get(entity_id, entity_id)


def _score(
    cases: tuple[Mem0EntityQualityCase, ...],
    results: tuple[Mem0EntityCaseResult, ...],
) -> dict[str, float | int]:
    counts = _Counts()
    total_duration = 0.0
    for case, result in zip(cases, results, strict=True):
        total_duration += result.duration_ms
        counts.errors += result.error is not None
        expected_entities = {_normalize(value) for value in case.expected_entities}
        actual_entities = {_normalize(value) for value in result.actual_entities}
        counts.expected_entities += len(expected_entities)
        counts.actual_entities += len(actual_entities)
        counts.entity_matches += len(expected_entities & actual_entities)
        _score_relationships(case, result, counts)
    possible_relations = counts.actual_relationships + counts.quarantined
    direction_attempts = counts.directed_matches + counts.reversed_matches
    metrics: dict[str, float | int] = {
        "case_count": len(cases),
        "error_count": counts.errors,
        "entity_precision": _ratio(counts.entity_matches, counts.actual_entities),
        "entity_recall": _ratio(counts.entity_matches, counts.expected_entities),
        "relationship_endpoint_precision": _ratio(
            counts.directed_matches, counts.actual_relationships
        ),
        "relationship_endpoint_recall": _ratio(
            counts.directed_matches, counts.expected_relationships
        ),
        "relationship_direction_accuracy": _ratio(
            counts.directed_matches, direction_attempts
        ),
        "predicate_fidelity": _ratio(counts.predicate_matches, counts.directed_matches),
        "evidence_precision": _ratio(counts.evidence_matches, counts.actual_evidence),
        "evidence_recall": _ratio(counts.evidence_matches, counts.expected_evidence),
        "evidence_relationships_scored": counts.evidence_relationships_scored,
        "quarantine_rate": _ratio(counts.quarantined, possible_relations, default=0.0),
        "empty_output_rate": _ratio(
            counts.empty_output_cases, counts.expected_nonempty_cases, default=0.0
        ),
        "mean_case_latency_ms": round(total_duration / len(cases), 3) if cases else 0.0,
        "relationships_expected": counts.expected_relationships,
        "relationships_returned": counts.actual_relationships,
        "relationships_quarantined": counts.quarantined,
    }
    return metrics


def _score_relationships(
    case: Mem0EntityQualityCase,
    result: Mem0EntityCaseResult,
    counts: _Counts,
) -> None:
    expected = case.expected_relationships
    actual = list(result.actual_relationships)
    counts.expected_relationships += len(expected)
    counts.actual_relationships += len(actual)
    counts.quarantined += result.relationships_quarantined
    if expected:
        counts.expected_nonempty_cases += 1
        if not actual:
            counts.empty_output_cases += 1
    unmatched = set(range(len(actual)))
    for wanted in expected:
        directed = [
            index
            for index in unmatched
            if _endpoints(actual[index]) == (_normalize(wanted.source), _normalize(wanted.target))
        ]
        matched_index = _prefer_predicate_match(directed, actual, wanted.accepted_predicates)
        if matched_index is not None:
            unmatched.remove(matched_index)
            counts.directed_matches += 1
            if _normalize(str(actual[matched_index]["predicate"])) in {
                _normalize(value) for value in wanted.accepted_predicates
            }:
                counts.predicate_matches += 1
            _score_evidence(wanted.evidence_source_ids, actual[matched_index], counts)
            continue
        reversed_candidates = [
            index
            for index in unmatched
            if _endpoints(actual[index]) == (_normalize(wanted.target), _normalize(wanted.source))
        ]
        if reversed_candidates:
            reverse_index = reversed_candidates[0]
            unmatched.remove(reverse_index)
            counts.reversed_matches += 1


def _prefer_predicate_match(
    indexes: list[int],
    actual: list[dict[str, Any]],
    accepted_predicates: tuple[str, ...],
) -> int | None:
    if not indexes:
        return None
    accepted = {_normalize(value) for value in accepted_predicates}
    return next(
        (
            index
            for index in indexes
            if _normalize(str(actual[index]["predicate"])) in accepted
        ),
        indexes[0],
    )


def _score_evidence(
    expected: tuple[str, ...], actual: dict[str, Any], counts: _Counts
) -> None:
    expected_ids = set(expected)
    actual_ids = set(actual["evidence_source_ids"])
    counts.expected_evidence += len(expected_ids)
    counts.actual_evidence += len(actual_ids)
    counts.evidence_matches += len(expected_ids & actual_ids)
    counts.evidence_relationships_scored += 1


def _endpoints(value: dict[str, Any]) -> tuple[str, str]:
    return _normalize(str(value["source"])), _normalize(str(value["target"]))


def _normalize(value: str) -> str:
    return " ".join(part for part in re.split(r"[^\w]+", value.casefold()) if part)


def _ratio(numerator: int, denominator: int, *, default: float = 1.0) -> float:
    return round(numerator / denominator, 6) if denominator else default


def _failed_thresholds(
    metrics: dict[str, float | int], thresholds: Mem0EntityQualityThresholds
) -> tuple[str, ...]:
    minimums = {
        "entity_precision": thresholds.entity_precision,
        "entity_recall": thresholds.entity_recall,
        "relationship_endpoint_precision": thresholds.relationship_endpoint_precision,
        "relationship_endpoint_recall": thresholds.relationship_endpoint_recall,
        "relationship_direction_accuracy": thresholds.relationship_direction_accuracy,
        "predicate_fidelity": thresholds.predicate_fidelity,
        "evidence_precision": thresholds.evidence_precision,
        "evidence_recall": thresholds.evidence_recall,
    }
    maximums = {
        "quarantine_rate": thresholds.maximum_quarantine_rate,
        "empty_output_rate": thresholds.maximum_empty_output_rate,
    }
    failed = [name for name, minimum in minimums.items() if metrics[name] < minimum]
    failed.extend(name for name, maximum in maximums.items() if metrics[name] > maximum)
    return tuple(failed)

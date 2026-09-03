from __future__ import annotations

import json
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from data_retrieval.mem0.admission import (
    Mem0AdmissionDisposition,
    Mem0VectorAdmissionPolicy,
)
from data_retrieval.retrieval.embedding import Embedder, cosine_similarity


@dataclass(frozen=True, slots=True)
class ColdStartProposalResult:
    case_id: str
    source: str
    predicate: str
    target: str
    evidence_source_ids: tuple[str, ...]
    semantically_correct: bool
    vector_similarity: float
    lexical_endpoint_support: bool
    disposition: str
    initial_weight: float


@dataclass(frozen=True, slots=True)
class ColdStartProfileResult:
    profile: str
    typed_edges: int
    correct_edges: int
    precision: float
    recall: float
    total_weight: float
    incorrect_weight: float


@dataclass(frozen=True, slots=True)
class Mem0ColdStartReport:
    experiment_id: str
    fixture_path: str
    mem0_report_path: str
    embedding_provider: str
    embedding_model: str
    policy_profile: str
    duration_ms: float
    experiment_passed: bool
    promotion_passed: bool
    expected_relationships: int
    profiles: tuple[ColdStartProfileResult, ...]
    proposals: tuple[ColdStartProposalResult, ...]
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            **asdict(self),
            "passed": self.experiment_passed,
            "profiles": [asdict(item) for item in self.profiles],
            "proposals": [asdict(item) for item in self.proposals],
        }


class Mem0ColdStartSuite:
    """Compare typed cold-start edges using one frozen real Mem0 proposal set."""

    def run(
        self,
        *,
        fixture_path: Path,
        mem0_report_path: Path,
        embedder: Embedder,
        policy: Mem0VectorAdmissionPolicy | None = None,
    ) -> Mem0ColdStartReport:
        started = time.perf_counter()
        selected_policy = policy or Mem0VectorAdmissionPolicy()
        fixture = _load_object(fixture_path)
        mem0_report = _load_object(mem0_report_path)
        experiment_id = f"{fixture.get('suite_id', 'unknown')}-cold-start-v1"
        if mem0_report.get("suite_id") != fixture.get("suite_id"):
            raise ValueError("Mem0 report and quality fixture suite IDs do not match")
        case_specs = {
            str(case["case_id"]): case
            for case in fixture.get("cases", ())
            if isinstance(case, dict)
        }
        report_cases = mem0_report.get("cases")
        if not isinstance(report_cases, list):
            raise ValueError("Mem0 quality report cases must be an array")
        raw = _raw_proposals(report_cases, case_specs)
        relationship_texts = tuple(
            f"{item['source']} {str(item['predicate']).replace('_', ' ')} {item['target']}"
            for item in raw
        )
        evidence_texts = tuple(
            _evidence_text(item, case_specs[str(item["case_id"])]) for item in raw
        )
        vectors = embedder.embed_documents((*relationship_texts, *evidence_texts))
        if len(vectors) != len(raw) * 2:
            raise ValueError("embedder returned the wrong number of cold-start vectors")
        split = len(raw)
        labels = _correctness_labels(raw, case_specs)
        proposals: list[ColdStartProposalResult] = []
        for index, item in enumerate(raw):
            similarity = min(
                1.0,
                max(0.0, cosine_similarity(vectors[index], vectors[split + index])),
            )
            evidence = evidence_texts[index]
            lexical = _contains(evidence, str(item["source"])) and _contains(
                evidence, str(item["target"])
            )
            disposition, weight, _ = selected_policy.classify(
                similarity=similarity,
                lexical_endpoint_support=lexical,
                confidence=1.0,
            )
            proposals.append(
                ColdStartProposalResult(
                    case_id=str(item["case_id"]),
                    source=str(item["source"]),
                    predicate=str(item["predicate"]),
                    target=str(item["target"]),
                    evidence_source_ids=tuple(
                        str(value) for value in item.get("evidence_source_ids", ())
                    ),
                    semantically_correct=labels[index],
                    vector_similarity=round(similarity, 6),
                    lexical_endpoint_support=lexical,
                    disposition=disposition.value,
                    initial_weight=round(weight, 6),
                )
            )
        expected_count = sum(
            len(case.get("expected_relationships", ())) for case in case_specs.values()
        )
        provisional = tuple(
            item
            for item in proposals
            if item.disposition == Mem0AdmissionDisposition.PROVISIONAL.value
        )
        oracle_reviewed = tuple(
            item
            for item in proposals
            if item.disposition == Mem0AdmissionDisposition.PROVISIONAL.value
            or (
                item.disposition == Mem0AdmissionDisposition.HOLD_FOR_REVIEW.value
                and item.semantically_correct
            )
        )
        profiles = (
            _profile("baseline", (), expected_count),
            _profile("vectors_only", (), expected_count),
            _profile("mem0_only", tuple(proposals), expected_count, raw_weight=1.0),
            _profile("mem0_vector_provisional", provisional, expected_count),
            _profile("mem0_vector_plus_oracle_review", oracle_reviewed, expected_count),
        )
        joint = profiles[3]
        thresholds = fixture.get("thresholds", {})
        minimum_precision = float(thresholds.get("relationship_endpoint_precision", 0.9))
        minimum_recall = float(thresholds.get("relationship_endpoint_recall", 0.9))
        promotion_passed = joint.precision >= minimum_precision and joint.recall >= minimum_recall
        return Mem0ColdStartReport(
            experiment_id=experiment_id,
            fixture_path=str(fixture_path),
            mem0_report_path=str(mem0_report_path),
            embedding_provider=embedder.provider,
            embedding_model=embedder.model,
            policy_profile=selected_policy.profile_id,
            duration_ms=(time.perf_counter() - started) * 1_000,
            experiment_passed=all(
                item.initial_weight <= selected_policy.provisional_weight_cap
                for item in proposals
            ),
            promotion_passed=promotion_passed,
            expected_relationships=expected_count,
            profiles=profiles,
            proposals=tuple(proposals),
            limitations=(
                "This scores typed-edge proposals, not end-to-end retrieval quality.",
                "The oracle-review profile is an upper-bound simulation, not a model result.",
                "One ten-case fixture cannot establish production thresholds.",
                "Vector similarity measures topical agreement, not predicate direction or truth.",
            ),
        )


def _load_object(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected one JSON object: {path}")
    return value


def _raw_proposals(
    report_cases: list[Any], case_specs: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    proposals: list[dict[str, Any]] = []
    for case in report_cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("case_id", ""))
        if case_id not in case_specs:
            raise ValueError(f"Mem0 report contains unknown case: {case_id}")
        actual = case.get("actual_relationships", ())
        if not isinstance(actual, list):
            raise ValueError(f"actual relationships must be an array for case {case_id}")
        proposals.extend({"case_id": case_id, **item} for item in actual if isinstance(item, dict))
    return proposals


def _evidence_text(proposal: dict[str, Any], case: dict[str, Any]) -> str:
    atoms = {
        str(atom["source_id"]): str(atom["content"])
        for atom in case.get("atoms", ())
        if isinstance(atom, dict)
    }
    return "\n\n".join(
        atoms[source_id]
        for source_id in proposal.get("evidence_source_ids", ())
        if source_id in atoms
    )


def _correctness_labels(
    proposals: list[dict[str, Any]], case_specs: dict[str, dict[str, Any]]
) -> tuple[bool, ...]:
    remaining = {
        case_id: list(case.get("expected_relationships", ()))
        for case_id, case in case_specs.items()
    }
    labels: list[bool] = []
    for proposal in proposals:
        expected = remaining[str(proposal["case_id"])]
        matched = next(
            (
                index
                for index, item in enumerate(expected)
                if _normalized(str(item.get("source", "")))
                == _normalized(str(proposal.get("source", "")))
                and _normalized(str(item.get("target", "")))
                == _normalized(str(proposal.get("target", "")))
                and _normalized(str(proposal.get("predicate", "")))
                in {
                    _normalized(str(value))
                    for value in item.get("accepted_predicates", ())
                }
            ),
            None,
        )
        labels.append(matched is not None)
        if matched is not None:
            expected.pop(matched)
    return tuple(labels)


def _profile(
    name: str,
    proposals: tuple[ColdStartProposalResult, ...],
    expected_count: int,
    *,
    raw_weight: float | None = None,
) -> ColdStartProfileResult:
    correct = sum(item.semantically_correct for item in proposals)
    weights = tuple(
        raw_weight if raw_weight is not None else item.initial_weight
        for item in proposals
    )
    total_weight = sum(weights)
    incorrect_weight = sum(
        weight
        for item, weight in zip(proposals, weights, strict=True)
        if not item.semantically_correct
    )
    return ColdStartProfileResult(
        profile=name,
        typed_edges=len(proposals),
        correct_edges=correct,
        precision=round(correct / len(proposals), 6) if proposals else 1.0,
        recall=round(correct / expected_count, 6) if expected_count else 1.0,
        total_weight=round(total_weight, 6),
        incorrect_weight=round(incorrect_weight, 6),
    )


def _contains(text: str, entity: str) -> bool:
    normalized_text = _normalized(text)
    needle = _normalized(entity)
    return bool(needle) and f" {needle} " in f" {normalized_text} "


def _normalized(value: str) -> str:
    return " ".join(part for part in re.split(r"[^\w]+", value.casefold()) if part)

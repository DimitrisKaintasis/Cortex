from __future__ import annotations

import json
import platform
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from data_retrieval.collective import (
    CollectivePolicy,
    ObservationOutcome,
    PrivateCandidate,
    RelationshipObservation,
    RelationshipState,
    ShadowCollectiveAggregator,
    ShadowCollectiveRanker,
    SharedConcept,
)
from data_retrieval.core.identifiers import content_hash


@dataclass(frozen=True, slots=True)
class ExperimentCheck:
    name: str
    passed: bool
    expected: Any
    actual: Any
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PolicyVariantResult:
    policy_id: str
    positive_target_rank: int
    positive_target_influence: float
    negative_target_rank: int
    negative_target_influence: float
    positive_support: float
    negative_support: float


@dataclass(frozen=True, slots=True)
class CollectiveTransferReport:
    suite_id: str
    fixture_path: str
    fixture_hash: str
    code_revision: str
    working_tree_dirty: bool
    python_version: str
    duration_ms: float
    artifact_location: str | None
    passed: bool
    checks: tuple[ExperimentCheck, ...]
    policy_variants: tuple[PolicyVariantResult, ...]
    baseline_ranking: tuple[dict[str, Any], ...]
    positive_ranking: tuple[dict[str, Any], ...]
    negative_ranking: tuple[dict[str, Any], ...]
    positive_snapshot: dict[str, Any]
    limitations: tuple[str, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "fixture_path": self.fixture_path,
            "fixture_hash": self.fixture_hash,
            "code_revision": self.code_revision,
            "working_tree_dirty": self.working_tree_dirty,
            "python_version": self.python_version,
            "duration_ms": self.duration_ms,
            "artifact_location": self.artifact_location,
            "passed": self.passed,
            "check_count": len(self.checks),
            "passed_check_count": sum(check.passed for check in self.checks),
            "checks": [asdict(check) for check in self.checks],
            "policy_variants": [asdict(item) for item in self.policy_variants],
            "baseline_ranking": list(self.baseline_ranking),
            "positive_ranking": list(self.positive_ranking),
            "negative_ranking": list(self.negative_ranking),
            "positive_snapshot": self.positive_snapshot,
            "limitations": list(self.limitations),
        }


class CollectiveTransferSuite:
    """Deterministic falsification harness for the collective-transfer architecture claim."""

    FORBIDDEN_OBSERVATION_FIELDS = frozenset(
        {
            "atom_id",
            "content",
            "document_id",
            "namespace",
            "payload",
            "public_user_id",
            "query",
            "session_id",
            "source_path",
        }
    )

    def run(
        self,
        fixture_path: Path,
        *,
        artifact_location: Path | None = None,
    ) -> CollectiveTransferReport:
        started = time.perf_counter()
        fixture_text = fixture_path.read_text(encoding="utf-8")
        fixture = json.loads(fixture_text)
        if not isinstance(fixture, dict) or fixture.get("fixture_id") != "collective-transfer-v1":
            raise ValueError(
                "collective transfer fixture must have fixture_id collective-transfer-v1"
            )
        as_of = self._datetime(fixture["as_of"])
        concepts = self._concepts(fixture)
        policy = self._policy(fixture["main_policy"])
        aggregator = ShadowCollectiveAggregator(policy)
        ranker = ShadowCollectiveRanker(bonus_weight=fixture["main_policy"]["bonus_weight"])
        positive = self._observations(
            fixture["positive_observations"], concepts=concepts, as_of=as_of, policy=policy
        )
        negative = self._observations(
            fixture["negative_observations"], concepts=concepts, as_of=as_of, policy=policy
        )
        query, candidates = self._case(fixture["storage_case"], concepts)
        unrelated_query, unrelated_candidates = self._case(fixture["unrelated_case"], concepts)

        empty_snapshot = aggregator.build((), as_of=as_of)
        positive_snapshot = aggregator.build(positive, as_of=as_of)
        negative_snapshot = aggregator.build((*positive, *negative), as_of=as_of)
        replay_snapshot = aggregator.build(tuple(reversed(positive)), as_of=as_of)
        duplicate_snapshot = aggregator.build((*positive, positive[0]), as_of=as_of)

        baseline = ranker.rank(
            query_concept_ids=query,
            candidates=candidates,
            snapshot=empty_snapshot,
        )
        positive_ranking = ranker.rank(
            query_concept_ids=query,
            candidates=candidates,
            snapshot=positive_snapshot,
        )
        negative_ranking = ranker.rank(
            query_concept_ids=query,
            candidates=candidates,
            snapshot=negative_snapshot,
        )
        unrelated_baseline = ranker.rank(
            query_concept_ids=unrelated_query,
            candidates=unrelated_candidates,
            snapshot=empty_snapshot,
        )
        unrelated_after = ranker.rank(
            query_concept_ids=unrelated_query,
            candidates=unrelated_candidates,
            snapshot=positive_snapshot,
        )

        target_id = fixture["storage_case"]["target_candidate_id"]
        positive_influence = positive_snapshot.influence(
            concepts["storage_query"].concept_id,
            concepts["postgresql"].concept_id,
        )
        negative_influence = negative_snapshot.influence(
            concepts["storage_query"].concept_id,
            concepts["postgresql"].concept_id,
        )
        lifecycle_checks = self._lifecycle_checks(
            concepts=concepts,
            as_of=as_of,
            policy=policy,
        )
        exported_fields = set(positive[0].canonical_payload())
        public_result = {
            "positive_snapshot": positive_snapshot.as_dict(),
            "baseline": [item.as_dict() for item in baseline],
            "positive": [item.as_dict() for item in positive_ranking],
            "negative": [item.as_dict() for item in negative_ranking],
        }
        serialized_public_result = json.dumps(public_result, sort_keys=True)
        leaked_sentinels = [
            sentinel
            for sentinel in fixture["private_sentinels"]
            if sentinel in serialized_public_result
        ]
        variants = self._policy_variants(
            concepts=concepts,
            positive=positive,
            negative=negative,
            as_of=as_of,
            query=query,
            candidates=candidates,
            target_id=target_id,
            bonus_weight=fixture["main_policy"]["bonus_weight"],
        )
        variant_by_id = {variant.policy_id: variant for variant in variants}

        checks = (
            self._check(
                "positive_transfer_target_rank",
                1,
                self._rank(positive_ranking, target_id),
            ),
            self._check(
                "positive_transfer_improves_rank",
                True,
                self._rank(positive_ranking, target_id) < self._rank(baseline, target_id),
            ),
            self._check(
                "negative_outcome_reduces_influence",
                True,
                negative_influence < positive_influence,
            ),
            self._check("negative_outcome_target_rank", 2, self._rank(negative_ranking, target_id)),
            self._check(
                "unrelated_query_unchanged",
                [item.as_dict() for item in unrelated_baseline],
                [item.as_dict() for item in unrelated_after],
            ),
            self._check(
                "snapshot_removal_restores_baseline",
                [item.as_dict() for item in baseline],
                [
                    item.as_dict()
                    for item in ranker.rank(
                        query_concept_ids=query,
                        candidates=candidates,
                        snapshot=empty_snapshot,
                    )
                ],
            ),
            self._check("order_independent_replay", positive_snapshot, replay_snapshot),
            self._check("idempotent_observation_replay", positive_snapshot, duplicate_snapshot),
            self._check("private_sentinel_leakage", [], leaked_sentinels),
            self._check(
                "forbidden_export_fields",
                [],
                sorted(exported_fields.intersection(self.FORBIDDEN_OBSERVATION_FIELDS)),
            ),
            self._check(
                "maximum_reference_is_outlier_sensitive",
                2,
                variant_by_id["maximum-reference-with-outlier"].positive_target_rank,
            ),
            self._check(
                "robust_reference_resists_single_outlier",
                1,
                variant_by_id["percentile-reference-with-outlier"].positive_target_rank,
            ),
            *lifecycle_checks,
        )
        revision, dirty = self._git_identity(fixture_path.parent)
        return CollectiveTransferReport(
            suite_id=fixture["fixture_id"],
            fixture_path=str(fixture_path),
            fixture_hash=content_hash(fixture_text),
            code_revision=revision,
            working_tree_dirty=dirty,
            python_version=platform.python_version(),
            duration_ms=(time.perf_counter() - started) * 1_000,
            artifact_location=str(artifact_location) if artifact_location else None,
            passed=all(check.passed for check in checks),
            checks=checks,
            policy_variants=variants,
            baseline_ranking=tuple(item.as_dict() for item in baseline),
            positive_ranking=tuple(item.as_dict() for item in positive_ranking),
            negative_ranking=tuple(item.as_dict() for item in negative_ranking),
            positive_snapshot=positive_snapshot.as_dict(),
            limitations=(
                "Synthetic concept mappings do not measure real-model concept alignment.",
                "Opaque contributor buckets reduce direct identity exposure but remain "
                "linkable within an epoch.",
                "Absence of fixture sentinels is a boundary regression check, not a privacy proof.",
                "No production consent, sensitivity, secure aggregation, or erasure mechanism "
                "is implemented.",
            ),
        )

    def _lifecycle_checks(
        self,
        *,
        concepts: dict[str, SharedConcept],
        as_of: datetime,
        policy: CollectivePolicy,
    ) -> tuple[ExperimentCheck, ...]:
        source = concepts["storage_query"].concept_id
        target = concepts["postgresql"].concept_id
        repeated = tuple(
            RelationshipObservation.create(
                local_event_key=f"repeat-{index}",
                contributor_bucket="repeat-contributor",
                source_concept_id=source,
                target_concept_id=target,
                outcome=ObservationOutcome.POSITIVE,
                support=0.3,
                observed_at=as_of,
                policy_version=policy.policy_id,
            )
            for index in range(10)
        )
        independent = RelationshipObservation.create(
            local_event_key="independent-1",
            contributor_bucket="independent-contributor",
            source_concept_id=source,
            target_concept_id=target,
            outcome=ObservationOutcome.POSITIVE,
            support=1.0,
            observed_at=as_of,
            policy_version=policy.policy_id,
        )
        capped = ShadowCollectiveAggregator(policy).build(repeated, as_of=as_of).relationships[0]
        accumulated = ShadowCollectiveAggregator(policy).build(
            (*repeated, independent), as_of=as_of
        ).relationships[0]

        one = repeated[0]
        half = ShadowCollectiveAggregator(policy).build(
            (one,), as_of=as_of + timedelta(days=policy.half_life_days or 0)
        ).relationships[0]
        dormant_time = as_of + timedelta(days=(policy.half_life_days or 30) * 4)
        dormant = ShadowCollectiveAggregator(policy).build(
            (one,), as_of=dormant_time
        ).relationships[0]
        reactivation = RelationshipObservation.create(
            local_event_key="reactivate-1",
            contributor_bucket="independent-contributor",
            source_concept_id=source,
            target_concept_id=target,
            outcome=ObservationOutcome.POSITIVE,
            support=1.0,
            observed_at=dormant_time,
            policy_version=policy.policy_id,
        )
        reactivated = ShadowCollectiveAggregator(policy).build(
            (one, reactivation), as_of=dormant_time
        ).relationships[0]
        inhibition = RelationshipObservation.create(
            local_event_key="inhibit-1",
            contributor_bucket="negative-contributor",
            source_concept_id=source,
            target_concept_id=target,
            outcome=ObservationOutcome.NEGATIVE,
            support=1.0,
            observed_at=as_of,
            policy_version=policy.policy_id,
        )
        inhibited = ShadowCollectiveAggregator(policy).build(
            (independent, inhibition), as_of=as_of
        ).relationships[0]
        return (
            self._check(
                "single_contributor_cap",
                policy.contributor_support_cap,
                capped.positive_support,
            ),
            self._check("independent_support_accumulates", 2.0, accumulated.positive_support),
            self._check("half_life_is_proportional", 0.15, round(half.positive_support, 10)),
            self._check(
                "unused_relationship_becomes_dormant",
                RelationshipState.DORMANT,
                dormant.state,
            ),
            self._check(
                "renewed_independent_evidence_reactivates",
                True,
                reactivated.state is not RelationshipState.DORMANT,
            ),
            self._check(
                "verified_negative_can_inhibit",
                RelationshipState.INHIBITED,
                inhibited.state,
            ),
        )

    def _policy_variants(
        self,
        *,
        concepts: dict[str, SharedConcept],
        positive: tuple[RelationshipObservation, ...],
        negative: tuple[RelationshipObservation, ...],
        as_of: datetime,
        query: tuple[str, ...],
        candidates: tuple[PrivateCandidate, ...],
        target_id: str,
        bonus_weight: float,
    ) -> tuple[PolicyVariantResult, ...]:
        outlier = RelationshipObservation.create(
            local_event_key="outlier-1",
            contributor_bucket="outlier-contributor",
            source_concept_id=concepts["storage_query"].concept_id,
            target_concept_id=concepts["outlier"].concept_id,
            outcome=ObservationOutcome.POSITIVE,
            support=100.0,
            observed_at=as_of,
            policy_version="normalization-outlier-v1",
        )
        definitions = (
            (
                CollectivePolicy(policy_id="control-no-learning", learning_enabled=False),
                positive,
            ),
            (
                CollectivePolicy(
                    policy_id="original-bounded-0-1",
                    initial_observed_support=0.5,
                    contributor_support_cap=1.0,
                    relationship_support_cap=1.0,
                    half_life_days=None,
                    keep_negative_separate=False,
                ),
                positive,
            ),
            (
                CollectivePolicy(policy_id="unbounded-no-decay", half_life_days=None),
                positive,
            ),
            (
                CollectivePolicy(
                    policy_id="unbounded-proportional-decay", half_life_days=30.0
                ),
                positive,
            ),
            (
                CollectivePolicy(
                    policy_id="unbounded-separated-positive-negative",
                    half_life_days=30.0,
                    keep_negative_separate=True,
                ),
                positive,
            ),
            (
                CollectivePolicy(
                    policy_id="maximum-reference-with-outlier",
                    contributor_support_cap=100.0,
                    half_life_days=None,
                    normalization="maximum",
                ),
                (*positive, outlier),
            ),
            (
                CollectivePolicy(
                    policy_id="percentile-reference-with-outlier",
                    contributor_support_cap=100.0,
                    half_life_days=None,
                    normalization="percentile",
                    reference_percentile=0.5,
                ),
                (*positive, outlier),
            ),
        )
        source = concepts["storage_query"].concept_id
        target = concepts["postgresql"].concept_id
        ranker = ShadowCollectiveRanker(bonus_weight=bonus_weight)
        results: list[PolicyVariantResult] = []
        for policy, positive_inputs in definitions:
            aggregator = ShadowCollectiveAggregator(policy)
            positive_snapshot = aggregator.build(positive_inputs, as_of=as_of)
            negative_snapshot = aggregator.build((*positive_inputs, *negative), as_of=as_of)
            positive_ranking = ranker.rank(
                query_concept_ids=query, candidates=candidates, snapshot=positive_snapshot
            )
            negative_ranking = ranker.rank(
                query_concept_ids=query, candidates=candidates, snapshot=negative_snapshot
            )
            projection = next(
                (
                    item
                    for item in negative_snapshot.relationships
                    if item.source_concept_id == source and item.target_concept_id == target
                ),
                None,
            )
            results.append(
                PolicyVariantResult(
                    policy_id=policy.policy_id,
                    positive_target_rank=self._rank(positive_ranking, target_id),
                    positive_target_influence=positive_snapshot.influence(source, target),
                    negative_target_rank=self._rank(negative_ranking, target_id),
                    negative_target_influence=negative_snapshot.influence(source, target),
                    positive_support=projection.positive_support if projection else 0.0,
                    negative_support=projection.negative_support if projection else 0.0,
                )
            )
        return tuple(results)

    @staticmethod
    def _policy(value: dict[str, Any]) -> CollectivePolicy:
        return CollectivePolicy(
            policy_id=value["policy_id"],
            contributor_support_cap=float(value["contributor_support_cap"]),
            half_life_days=float(value["half_life_days"]),
            negative_penalty=float(value["negative_penalty"]),
            normalization=value["normalization"],
            hot_threshold=float(value["hot_threshold"]),
            warm_threshold=float(value["warm_threshold"]),
            dormant_threshold=float(value["dormant_threshold"]),
        )

    @staticmethod
    def _concepts(fixture: dict[str, Any]) -> dict[str, SharedConcept]:
        return {
            alias: SharedConcept.from_key(value)
            for alias, value in fixture["concepts"].items()
        }

    @staticmethod
    def _observations(
        values: list[dict[str, Any]],
        *,
        concepts: dict[str, SharedConcept],
        as_of: datetime,
        policy: CollectivePolicy,
    ) -> tuple[RelationshipObservation, ...]:
        return tuple(
            RelationshipObservation.create(
                local_event_key=value["local_event_key"],
                contributor_bucket=value["contributor_bucket"],
                source_concept_id=concepts[value["source"]].concept_id,
                target_concept_id=concepts[value["target"]].concept_id,
                outcome=ObservationOutcome(value["outcome"]),
                support=float(value["support"]),
                observed_at=as_of,
                policy_version=policy.policy_id,
            )
            for value in values
        )

    @staticmethod
    def _case(
        value: dict[str, Any], concepts: dict[str, SharedConcept]
    ) -> tuple[tuple[str, ...], tuple[PrivateCandidate, ...]]:
        query = tuple(concepts[alias].concept_id for alias in value["query_concepts"])
        candidates = tuple(
            PrivateCandidate(
                candidate_id=item["candidate_id"],
                baseline_score=float(item["baseline_score"]),
                concept_ids=tuple(concepts[alias].concept_id for alias in item["concepts"]),
            )
            for item in value["candidates"]
        )
        return query, candidates

    @staticmethod
    def _rank(ranking: tuple[Any, ...], candidate_id: str) -> int:
        return next(
            index
            for index, item in enumerate(ranking, start=1)
            if item.candidate_id == candidate_id
        )

    @staticmethod
    def _check(name: str, expected: Any, actual: Any, detail: str = "") -> ExperimentCheck:
        return ExperimentCheck(
            name=name,
            passed=expected == actual,
            expected=CollectiveTransferSuite._json_value(expected),
            actual=CollectiveTransferSuite._json_value(actual),
            detail=detail,
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        if hasattr(value, "as_dict"):
            return value.as_dict()
        if hasattr(value, "value"):
            return value.value
        if isinstance(value, tuple | list):
            return [CollectiveTransferSuite._json_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): CollectiveTransferSuite._json_value(item)
                for key, item in value.items()
            }
        return value

    @staticmethod
    def _datetime(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError("fixture timestamps must be timezone-aware")
        return parsed

    @staticmethod
    def _git_identity(start: Path) -> tuple[str, bool]:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
        return revision.stdout.strip() or "unknown", bool(dirty.stdout.strip())

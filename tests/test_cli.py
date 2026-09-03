from __future__ import annotations

import unittest

from data_retrieval.cli import build_parser


class CliParserTests(unittest.TestCase):
    def test_retrieve_accepts_optional_query_tag_model(self) -> None:
        args = build_parser().parse_args(
            [
                "retrieve",
                "How does the powertrain work?",
                "--namespace",
                "project-a",
                "--tag-model",
                "query-tagger",
            ]
        )

        self.assertEqual(args.tag_model, "query-tagger")

    def test_evaluate_accepts_optional_query_tag_model(self) -> None:
        args = build_parser().parse_args(
            ["evaluate", "--tag-model", "query-tagger"]
        )

        self.assertEqual(args.tag_model, "query-tagger")

    def test_collective_transfer_experiment_has_isolated_defaults(self) -> None:
        args = build_parser().parse_args(["evaluate-collective-transfer"])

        self.assertEqual(args.fixture.as_posix(), "evals/collective_transfer_v1.json")
        self.assertEqual(args.report.as_posix(), "data/results/collective-transfer-v1.json")

    def test_review_cascade_experiment_has_isolated_defaults(self) -> None:
        args = build_parser().parse_args(["evaluate-review-cascade"])

        self.assertEqual(args.fixture.as_posix(), "evals/review_cascade_v1.json")
        self.assertEqual(args.report.as_posix(), "data/results/review-cascade-v1.json")

    def test_mem0_entity_quality_gate_has_isolated_defaults(self) -> None:
        args = build_parser().parse_args(
            ["evaluate-mem0-entities", "--case-id", "person-leads-project"]
        )

        self.assertEqual(args.fixture.as_posix(), "evals/mem0_entity_quality_v1.json")
        self.assertEqual(
            args.mem0_config.as_posix(), "evals/mem0_entity_smoke_config.json"
        )
        self.assertEqual(
            args.report.as_posix(), "data/results/mem0-entity-quality-v1.json"
        )
        self.assertEqual(args.case_ids, ["person-leads-project"])

    def test_mem0_vector_calibration_exposes_bounded_policy_controls(self) -> None:
        args = build_parser().parse_args(
            [
                "calibrate-mem0-vectors",
                "--namespace",
                "project-a",
                "--embedding-model",
                "embedder-v1",
                "--provisional-weight-cap",
                "0.2",
            ]
        )

        self.assertEqual(args.namespace, "project-a")
        self.assertEqual(args.embedding_model, "embedder-v1")
        self.assertEqual(args.provisional_weight_cap, 0.2)

    def test_mem0_cold_start_comparison_uses_frozen_report(self) -> None:
        args = build_parser().parse_args(
            ["evaluate-mem0-cold-start", "--embedding-model", "embedder-v1"]
        )

        self.assertEqual(args.fixture.as_posix(), "evals/mem0_entity_quality_v1.json")
        self.assertEqual(
            args.mem0_report.as_posix(), "data/results/mem0-entity-quality-v1.json"
        )
        self.assertEqual(args.embedding_model, "embedder-v1")

    def test_mem0_experience_exposes_feedback_selection(self) -> None:
        args = build_parser().parse_args(
            [
                "evaluate-mem0-experience",
                "--embedding-model",
                "embedder-v1",
                "--feedback-selection",
                "all_relevant",
                "--usage-rounds",
                "3",
            ]
        )

        self.assertEqual(args.fixture.as_posix(), "evals/mem0_experience_v1.json")
        self.assertEqual(args.feedback_selection, "all_relevant")
        self.assertEqual(args.usage_rounds, 3)

    def test_repository_feature_observer_accepts_cached_embedding_identity(self) -> None:
        args = build_parser().parse_args(
            [
                "observe-repository-features",
                "--namespace",
                "project-a",
                "--embedding-provider",
                "ollama",
                "--embedding-model",
                "harrier-v1",
                "--limit",
                "25",
            ]
        )

        self.assertEqual(args.embedding_provider, "ollama")
        self.assertEqual(args.embedding_model, "harrier-v1")
        self.assertEqual(args.limit, 25)

    def test_longmemeval_can_explicitly_enable_benchmark_tag_resolution(self) -> None:
        args = build_parser().parse_args(
            [
                "run-longmemeval",
                "dataset.json",
                "--dataset-id",
                "dev20",
                "--resolve-benchmark-tags",
                "--benchmark-tag-min-confidence",
                "0.7",
            ]
        )

        self.assertTrue(args.resolve_benchmark_tags)
        self.assertEqual(args.benchmark_tag_min_confidence, 0.7)

    def test_longmemeval_can_run_evaluation_without_reenrichment(self) -> None:
        args = build_parser().parse_args(
            [
                "run-longmemeval",
                "dataset.json",
                "--dataset-id",
                "dev20",
                "--evaluation-only",
                "--tag-model",
                "query-tagger",
                "--embedding-model",
                "query-embedder",
            ]
        )

        self.assertTrue(args.evaluation_only)
        self.assertEqual(args.tag_model, "query-tagger")

    def test_longmemeval_ablation_accepts_repeatable_top_k_values(self) -> None:
        args = build_parser().parse_args(
            [
                "evaluate-longmemeval-ablation",
                "dataset.json",
                "--dataset-id",
                "dev20",
                "--top-k",
                "3",
                "--top-k",
                "10",
            ]
        )

        self.assertEqual(args.top_ks, [3, 10])

    def test_tag_candidate_review_commands_are_exposed(self) -> None:
        listed = build_parser().parse_args(
            ["list-tag-candidates", "--namespace", "project-a", "--state", "proposed"]
        )
        resolved = build_parser().parse_args(
            [
                "resolve-tag-candidate",
                "candidate-1",
                "--action",
                "merge",
                "--canonical-tag",
                "database",
            ]
        )

        self.assertEqual(listed.state, "proposed")
        self.assertEqual(resolved.action, "merge")
        self.assertEqual(resolved.canonical_tag, "database")

    def test_weight_audit_can_explicitly_request_aggregate_repair(self) -> None:
        args = build_parser().parse_args(
            [
                "audit-weights",
                "--namespace",
                "project-a",
                "--repair-aggregates",
            ]
        )

        self.assertTrue(args.repair_aggregates)


if __name__ == "__main__":
    unittest.main()

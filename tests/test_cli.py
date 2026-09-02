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

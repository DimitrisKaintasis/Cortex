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


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from data_retrieval.storage.sqlite import SQLiteRepository
from scripts.run_learning_contribution import (
    OfflineEmbedder,
    copy_database,
    digest,
    invariants,
    paired_changes,
    summarize,
)


def row(query_id="a", group="transfer", rr=0.5, recall=0.5):
    return {
        "id": query_id,
        "group": group,
        "query_text": "query",
        "turn_reciprocal_rank": rr,
        "turn_recall": recall,
        "turn_hit": rr > 0,
        "direct_turn_reciprocal_rank": rr,
        "direct_turn_recall": recall,
        "retrieved_atom_ids": ["one", "two"],
    }


class LearningContributionTests(unittest.TestCase):
    def test_macro_metrics_separate_collateral_anchors(self):
        result = summarize(
            [
                row(rr=1, recall=1),
                row("b", rr=0, recall=0),
                row("c", "collateral", rr=0.25, recall=1),
            ]
        )
        self.assertEqual(result["transfer"]["mrr"], 0.5)
        self.assertEqual(result["transfer"]["hit"], 0.5)
        self.assertEqual(result["collateral"]["mrr"], 0.25)
        self.assertEqual(result["collateral"]["query_count"], 1)

    def test_pairing_matches_ids_not_row_positions(self):
        compared = paired_changes([row(), row("b", rr=0.25)], [row("b", rr=1), row()])
        self.assertEqual(compared[0]["mrr_before"], 0.25)
        self.assertEqual(compared[0]["mrr_after"], 1)
        self.assertFalse(compared[0]["ranking_changed"])

    def test_pairing_rejects_missing_and_duplicate_ids(self):
        for after in ([row("b")], [row(), row()]):
            with self.assertRaises(ValueError):
                paired_changes([row()], after)

    def test_scoring_embedder_fails_closed_without_cached_vectors(self):
        embedder = OfflineEmbedder("test-model")
        with self.assertRaises(AssertionError):
            embedder.embed_query("query")
        with self.assertRaises(AssertionError):
            embedder.embed_documents(("document",))

    def test_copy_and_invariant_checks_do_not_mutate_source(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "source.sqlite3"
            destination = Path(directory) / "copy.sqlite3"
            repository = SQLiteRepository(source)
            repository.close()
            before = digest(source)
            copy_database(source, destination)
            self.assertEqual(invariants(source), invariants(destination))
            self.assertEqual(before, digest(source))
            with self.assertRaises(ValueError):
                copy_database(source, destination)
            with closing(sqlite3.connect(destination)) as connection:
                connection.execute(
                    "INSERT INTO documents VALUES (?,?,?,?,?,?)",
                    ("d", "n", "source", "hash", "2026-09-05T00:00:00+00:00", "{}"),
                )
                connection.commit()
            self.assertNotEqual(invariants(source), invariants(destination))
            self.assertEqual(before, digest(source))


if __name__ == "__main__":
    unittest.main()

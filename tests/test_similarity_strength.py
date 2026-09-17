import sqlite3
import tempfile
import unittest
from pathlib import Path

from scripts.run_similarity_strength import scale_priors


class SimilarityStrengthTests(unittest.TestCase):
    def test_only_development_similarity_weights_change(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "test.sqlite3"
            connection = sqlite3.connect(path)
            try:
                connection.executescript("""
                    CREATE TABLE tags(tag_id TEXT, namespace TEXT);
                    CREATE TABLE tag_relations(source_tag_id TEXT, target_tag_id TEXT,
                                               relation_type TEXT, weight_raw REAL);
                """)
                connection.executemany(
                    "INSERT INTO tags VALUES (?,?)",
                    [
                        ("a", "locomo-learning-v1:conv-26"),
                        ("b", "locomo-learning-v1:conv-26"),
                        ("c", "other"),
                        ("d", "other"),
                    ],
                )
                connection.executemany(
                    "INSERT INTO tag_relations VALUES (?,?,?,?)",
                    [
                        ("a", "b", "semantic_similarity", 0.2),
                        ("a", "b", "co_occurs", 0.4),
                        ("c", "d", "semantic_similarity", 0.2),
                    ],
                )
                connection.commit()
                self.assertEqual(scale_priors(path, 10), 1)
                self.assertEqual(
                    connection.execute("SELECT weight_raw FROM tag_relations").fetchall(),
                    [(2.0,), (0.4,), (0.2,)],
                )
                with self.assertRaises(ValueError):
                    scale_priors(path, 5)
            finally:
                connection.close()

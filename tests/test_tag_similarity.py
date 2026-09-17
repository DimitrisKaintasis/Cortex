import math
import tempfile
import unittest
from pathlib import Path

from data_retrieval.calibration.tag_similarity import (
    TagSimilarityCalibrationService,
    TagSimilarityPolicy,
)
from data_retrieval.retrieval.models import QueryPlan
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer


class Embedder:
    provider = "test"
    model = "fixed"
    calls = 0

    def embed_documents(self, texts):
        self.calls += 1
        vectors = {
            "dance": (1.0, 0.0),
            "contemporary dance": (0.8, 0.6),
            "dance alias": (0.99, math.sqrt(1 - 0.99**2)),
            "unrelated": (0.0, -1.0),
        }
        return tuple(vectors[t] for t in texts)


class TagSimilarityTests(unittest.TestCase):
    def test_sqlite_calibration_is_bounded_replay_safe_and_auditable(self):
        with tempfile.TemporaryDirectory() as folder:
            repository = SQLiteRepository(Path(folder) / "test.sqlite3")
            try:
                for tag in ("dance", "contemporary dance", "dance alias", "unrelated"):
                    IngestService(repository).ingest_text(
                        namespace="test", source=tag, text=tag, explicit_tags=(tag,)
                    )
                embedder = Embedder()
                canonicalizer = SemanticTagCanonicalizer(embedder)
                catalog = tuple(repository.list_tags("test", limit=500))
                canonicalizer.resolve(candidates=("dance",), catalog=catalog)
                calls = embedder.calls
                service = TagSimilarityCalibrationService(
                    repository, canonicalizer, TagSimilarityPolicy(maximum_neighbors=1)
                )
                result = service.calibrate("test")
                self.assertEqual(embedder.calls, calls)
                self.assertEqual(result["created"], 1)
                relations = repository.get_tag_relations_touching(
                    tag_ids=tuple(t.tag_id for t in catalog), relation_type="semantic_similarity"
                )
                self.assertEqual(len(relations), 1)
                self.assertLessEqual(relations[0].weight_raw, 0.25)
                by_id = {t.tag_id: t for t in catalog}
                relation = relations[0]
                query_tag = by_id[relation.source_tag_id].canonical_text
                destination_tag = by_id[relation.target_tag_id].canonical_text
                retrieved = RetrievalService(repository).retrieve(
                    QueryPlan(
                        query="unmatched query words", namespace="test", query_tags=(query_tag,)
                    )
                )
                destination = next(i for i in retrieved.items if i.content == destination_tag)
                self.assertGreater(destination.score.relationship, 0)
                self.assertEqual(destination.score.tag, 0)
                before = repository.list_tag_relations(namespace="test")
                self.assertEqual(service.calibrate("test")["created"], 0)
                self.assertEqual(repository.list_tag_relations(namespace="test"), before)
                self.assertTrue(WeightLedgerService(repository).audit_namespace("test").passed)
                self.assertEqual(service.calibrate("empty")["created"], 0)
            finally:
                repository.close()

    def test_invalid_thresholds(self):
        with self.assertRaises(ValueError):
            TagSimilarityPolicy(lower_threshold=float("nan"))
        with self.assertRaises(ValueError):
            TagSimilarityCalibrationService(
                None,
                SemanticTagCanonicalizer(Embedder()),
                TagSimilarityPolicy(lower_threshold=0.95),
            )

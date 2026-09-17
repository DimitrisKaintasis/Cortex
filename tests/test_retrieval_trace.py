import unittest

from data_retrieval.domain.models import AtomLink, AtomLinkRelation, IngestionBundle
from data_retrieval.retrieval.models import QueryPlan
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.memory import InMemoryRepository


class RetrievalTraceTests(unittest.TestCase):
    def test_route_contributions_reconcile(self):
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        left = ingestion.ingest_text(namespace="routes", source="a", text="Dance competition.")
        right = ingestion.ingest_text(namespace="routes", source="b", text="Finding Freedom.")
        repository.persist_ingestion(
            IngestionBundle(
                document=repository.get_document(right.document_id),
                atoms=repository.get_atoms_for_document(right.document_id),
                tags=(),
                atom_tags=(),
                atom_links=(
                    AtomLink(
                        from_atom_id=left.atom_ids[0],
                        to_atom_id=right.atom_ids[0],
                        relation=AtomLinkRelation.ADJACENT_TO,
                    ),
                ),
            )
        )
        plan = QueryPlan(query="Dance", namespace="routes")
        baseline = RetrievalService(repository).retrieve(plan)
        events = []
        traced = RetrievalService(repository, diagnostic_sink=events.append).retrieve(plan)
        self.assertEqual(baseline.items, traced.items)
        candidates = next(e["candidates"] for e in events if "candidates" in e)
        target = next(c for c in candidates if c["atom_id"] == right.atom_ids[0])
        self.assertFalse(target["base_seed"])
        contributions = [
            e["raw_contribution"] for e in events if e.get("route_destination") == right.atom_ids[0]
        ]
        self.assertTrue(contributions)
        self.assertAlmostEqual(sum(contributions), target["raw"]["relationship"])

    def test_trace_preserves_ranking_and_scores(self):
        repository = InMemoryRepository()
        for text in ("Dance team won a competition.", "Dance piece Finding Freedom."):
            IngestService(repository).ingest_text(
                namespace="trace-test", source=text, text=text, explicit_tags=("dance",)
            )
        plan = QueryPlan(query="dance competition", namespace="trace-test", top_k=1)
        baseline = RetrievalService(repository).retrieve(plan)
        events = []
        traced = RetrievalService(repository, diagnostic_sink=events.append).retrieve(plan)
        self.assertEqual(baseline.items, traced.items)
        self.assertEqual(baseline.diagnostics, traced.diagnostics)
        summary = next(e for e in events if "candidates" in e)
        self.assertEqual(len(summary["candidates"]), 2)
        self.assertEqual(summary["candidates"][0]["selected_rank"], 1)
        self.assertIsNone(summary["candidates"][1]["selected_rank"])

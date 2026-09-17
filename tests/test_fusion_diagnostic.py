import unittest

from data_retrieval.domain.models import AtomKind
from data_retrieval.retrieval.models import RetrievalItem, ScoreBreakdown
from scripts.run_fusion_diagnostic import agreement_rerank


def item(name, tag, semantic):
    return RetrievalItem(
        name,
        name,
        next(iter(AtomKind)),
        None,
        "source",
        ScoreBreakdown(tag=tag, semantic=semantic, final=0.45 * tag + 0.30 * semantic),
        {},
    )


class FusionDiagnosticTests(unittest.TestCase):
    def test_agreement_preserves_default_formula(self):
        original = [item("a", 1, 1), item("b", 0.5, 0.5)]
        ranked, gate = agreement_rerank(original, 1)
        self.assertEqual(gate, 1)
        self.assertEqual(ranked, original)

    def test_disagreement_transfers_tag_weight_without_mutation(self):
        original = [item("a", 1, 0), item("b", 0, 1)]
        ranked, gate = agreement_rerank(original, 1)
        self.assertEqual(gate, 0)
        self.assertEqual(ranked[0].atom_id, "b")
        self.assertAlmostEqual(ranked[0].score.final, 0.75)
        self.assertEqual(original[0].score.final, 0.45)

    def test_absent_semantics_does_not_penalize_tags(self):
        original = [item("a", 1, 0)]
        ranked, gate = agreement_rerank(original, 1)
        self.assertEqual(gate, 1)
        self.assertEqual(ranked, original)

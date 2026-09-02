from __future__ import annotations

import unittest

from data_retrieval.domain.models import AtomKind, AtomRole
from data_retrieval.retrieval.models import RetrievalItem, ScoreBreakdown
from data_retrieval.retrieval.packing import EvidencePacker


def _item(
    atom_id: str,
    *,
    role: AtomRole,
    score: float,
    content: str | None = None,
    lineage: tuple[str, ...] = (),
) -> RetrievalItem:
    return RetrievalItem(
        atom_id=atom_id,
        content=content or atom_id,
        kind=AtomKind.SOURCE,
        occurred_at=None,
        role=role.value,
        score=ScoreBreakdown(final=score),
        metadata={},
        lineage_atom_ids=lineage,
        atom_role=role,
    )


class EvidencePackerTests(unittest.TestCase):
    def test_source_quota_survives_higher_ranked_derived_candidates(self) -> None:
        ranked = [
            _item("derived-1", role=AtomRole.DERIVED, score=1.0, content="alpha"),
            _item("derived-2", role=AtomRole.DERIVED, score=0.9, content="bravo"),
            _item("derived-3", role=AtomRole.DERIVED, score=0.8, content="charlie"),
            _item("source-1", role=AtomRole.SOURCE, score=0.4),
            _item("source-2", role=AtomRole.SOURCE, score=0.3),
        ]

        packed = EvidencePacker().pack(ranked, top_k=5)

        self.assertEqual(
            {item.atom_id for item in packed.items},
            {"derived-1", "derived-2", "source-1", "source-2"},
        )
        self.assertEqual(packed.diagnostics["source_target"], 2)
        self.assertEqual(packed.diagnostics["source_selected"], 2)
        self.assertEqual(packed.diagnostics["excluded"], {"derived_cap": 1})

    def test_derived_cap_can_intentionally_underfill_an_evidence_pack(self) -> None:
        ranked = [
            _item(atom_id, role=AtomRole.DERIVED, score=1.0 - index / 10, content=content)
            for index, (atom_id, content) in enumerate(
                (
                    ("derived-0", "alpha"),
                    ("derived-1", "bravo"),
                    ("derived-2", "charlie"),
                    ("derived-3", "delta"),
                    ("derived-4", "echo"),
                )
            )
        ]
        ranked.append(_item("source", role=AtomRole.SOURCE, score=0.1))

        packed = EvidencePacker().pack(ranked, top_k=4)

        self.assertEqual(len(packed.items), 3)
        self.assertEqual(packed.diagnostics["derived_limit"], 2)
        self.assertTrue(packed.diagnostics["underfilled"])

    def test_lineage_equivalent_derived_items_collapse(self) -> None:
        ranked = [
            _item(
                "fact",
                role=AtomRole.DERIVED,
                score=1.0,
                content="A precise extracted fact.",
                lineage=("source-1",),
            ),
            _item(
                "summary",
                role=AtomRole.DERIVED,
                score=0.9,
                content="A longer temporal interpretation.",
                lineage=("source-1",),
            ),
            _item("other", role=AtomRole.DERIVED, score=0.8, lineage=("source-2",)),
        ]

        packed = EvidencePacker().pack(ranked, top_k=3)

        self.assertEqual([item.atom_id for item in packed.items], ["fact", "other"])
        self.assertEqual(packed.diagnostics["excluded"], {"lineage_equivalent": 1})

    def test_near_duplicate_derived_text_collapses_without_lineage(self) -> None:
        ranked = [
            _item(
                "first",
                role=AtomRole.DERIVED,
                score=1.0,
                content="Mac Mini runs inference overnight.",
            ),
            _item(
                "second",
                role=AtomRole.DERIVED,
                score=0.9,
                content="Mac Mini runs inference overnight!",
            ),
        ]

        packed = EvidencePacker().pack(ranked, top_k=2)

        self.assertEqual([item.atom_id for item in packed.items], ["first"])
        self.assertEqual(packed.diagnostics["excluded"], {"near_duplicate": 1})


if __name__ == "__main__":
    unittest.main()

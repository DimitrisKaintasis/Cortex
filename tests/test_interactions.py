from __future__ import annotations

import unittest

from data_retrieval.domain.models import AtomKind, AtomLinkRelation
from data_retrieval.retrieval.models import QueryPlan
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.interactions import InteractionService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.storage.memory import InMemoryRepository


class InteractionServiceTests(unittest.TestCase):
    def test_records_turn_evidence_lineage_and_outcome(self) -> None:
        repository = InMemoryRepository()
        source = IngestService(repository).ingest_text(
            namespace="project-a",
            source="fact",
            text="PostgreSQL is the canonical database.",
            explicit_tags=("database",),
        )
        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(query="database", namespace="project-a", query_tags=("database",))
        )

        result = InteractionService(repository).record_turn(
            namespace="project-a",
            conversation_id="conversation-1",
            turn_id="turn-1",
            user_text="Which database is canonical?",
            assistant_text="PostgreSQL is canonical.",
            retrieval_id=retrieval.retrieval_id,
            used_atom_ids=(source.atom_ids[0],),
            outcome="positive",
            tags=("database",),
        )

        self.assertIsNotNone(result.feedback)
        assistant = repository.get_atom(result.assistant_atom_ids[0])
        self.assertIsNotNone(assistant)
        assert assistant is not None
        self.assertEqual(assistant.kind, AtomKind.INTERACTION)
        links = repository.get_atom_links(assistant.atom_id)
        evidence = [link for link in links if link.relation is AtomLinkRelation.DERIVED_FROM]
        self.assertEqual(len(evidence), 1)
        self.assertEqual(evidence[0].to_atom_id, source.atom_ids[0])

    def test_rejects_unattributable_use_before_writing_interaction(self) -> None:
        repository = InMemoryRepository()
        source = IngestService(repository).ingest_text(
            namespace="project-a", source="fact", text="A stored fact."
        )
        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(query="stored", namespace="project-a")
        )
        before = repository.document_count

        with self.assertRaisesRegex(ValueError, "used atoms must have been returned"):
            InteractionService(repository).record_turn(
                namespace="project-a",
                conversation_id="conversation-1",
                turn_id="turn-1",
                user_text="Question",
                assistant_text="Answer",
                retrieval_id=retrieval.retrieval_id,
                used_atom_ids=("not-returned", source.atom_ids[0]),
            )

        self.assertEqual(repository.document_count, before)


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from data_retrieval.domain.models import TagCandidateState, TagLevel, TagOrigin
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.services.tag_lifecycle import TagLifecycleService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.proposals import TagProposal


class FixedProposer:
    evidence_source = "stub:review-model"
    proposal_version = "review-v1"

    def __init__(self, proposal: TagProposal) -> None:
        self.proposal = proposal

    def propose_tags(self, *, text, namespace, existing_tags):
        return (self.proposal,)


class TagLifecycleTests(unittest.TestCase):
    def test_novel_candidate_is_quarantined_until_promoted(self) -> None:
        repository = InMemoryRepository()
        ingested = IngestService(repository).ingest_text(
            namespace="project-a",
            source="worker-note",
            text="The Mac runs background inference jobs.",
        )
        enrichment = TagEnrichmentService(
            repository,
            FixedProposer(TagProposal("Remote Inference", 0.91, TagLevel.BROAD)),
        ).enrich_document(ingested.document_id)

        candidate = repository.get_tag_candidate(enrichment.candidate_ids[0])
        self.assertIsNotNone(candidate)
        self.assertIs(candidate.state, TagCandidateState.PROPOSED)
        self.assertEqual(repository.list_tags("project-a"), ())
        self.assertEqual(repository.atom_tags_for(ingested.atom_ids[0]), ())
        self.assertEqual(
            repository.search_tag_hits(
                namespace="project-a", canonical_tags=("remote inference",), limit=5
            ),
            (),
        )

        promoted = TagLifecycleService(repository).promote(candidate.candidate_id)

        self.assertIs(promoted.candidate.state, TagCandidateState.CANONICALIZED)
        self.assertEqual(promoted.tag.canonical_text, "remote inference")
        self.assertIs(promoted.atom_tag.origin, TagOrigin.PROMOTED_PROPOSAL)
        self.assertEqual(
            repository.search_tag_hits(
                namespace="project-a", canonical_tags=("remote inference",), limit=5
            )[0].atom_id,
            ingested.atom_ids[0],
        )

    def test_candidate_can_merge_into_existing_canonical_tag(self) -> None:
        repository = InMemoryRepository()
        IngestService(repository).ingest_text(
            namespace="project-a",
            source="catalog",
            text="The storage catalog.",
            explicit_tags=("database",),
        )
        target = IngestService(repository).ingest_text(
            namespace="project-a",
            source="target",
            text="PostgreSQL persists records.",
        )
        enrichment = TagEnrichmentService(
            repository,
            FixedProposer(TagProposal("Postgres Storage", 0.88, TagLevel.SPECIFIC)),
        ).enrich_document(target.document_id)

        result = TagLifecycleService(repository).merge(
            enrichment.candidate_ids[0], "database"
        )

        self.assertIs(result.candidate.state, TagCandidateState.MERGED)
        self.assertEqual(result.tag.aliases, ("postgres storage",))
        self.assertEqual(result.atom_tag.tag_id, result.tag.tag_id)

    def test_rejected_candidate_never_creates_serving_objects(self) -> None:
        repository = InMemoryRepository()
        target = IngestService(repository).ingest_text(
            namespace="project-a", source="target", text="An ambiguous note."
        )
        enrichment = TagEnrichmentService(
            repository,
            FixedProposer(TagProposal("Vague", 0.31, TagLevel.BROAD)),
        ).enrich_document(target.document_id)
        service = TagLifecycleService(repository)

        result = service.reject(enrichment.candidate_ids[0], reason="too vague")

        self.assertIs(result.candidate.state, TagCandidateState.REJECTED)
        self.assertEqual(repository.list_tags("project-a"), ())
        self.assertEqual(repository.atom_tags_for(target.atom_ids[0]), ())
        with self.assertRaisesRegex(ValueError, "already resolved"):
            service.promote(enrichment.candidate_ids[0])

    def test_sqlite_persists_candidate_and_atomic_promotion(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "lifecycle.sqlite3"
            with SQLiteRepository(path) as repository:
                target = IngestService(repository).ingest_text(
                    namespace="project-a", source="target", text="A durable proposal."
                )
                enrichment = TagEnrichmentService(
                    repository,
                    FixedProposer(TagProposal("Durable Concept", 0.8)),
                ).enrich_document(target.document_id)
                TagLifecycleService(repository).promote(enrichment.candidate_ids[0])

            with SQLiteRepository(path) as reopened:
                candidate = reopened.get_tag_candidate(enrichment.candidate_ids[0])
                self.assertIs(candidate.state, TagCandidateState.CANONICALIZED)
                self.assertEqual(
                    reopened.list_tags("project-a")[0].canonical_text,
                    "durable concept",
                )
                self.assertEqual(len(reopened.atom_tags_for(target.atom_ids[0])), 1)

    def test_sqlite_migration_preserves_explicit_and_quarantines_model_tags(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.sqlite3"
            with SQLiteRepository(path) as repository:
                trusted = IngestService(repository).ingest_text(
                    namespace="project-a",
                    source="trusted",
                    text="Trusted source.",
                    explicit_tags=("trusted",),
                )
                model_target = IngestService(repository).ingest_text(
                    namespace="project-a", source="model", text="Model source."
                )
                trusted_tag_id = repository.list_tags("project-a")[0].tag_id

            connection = sqlite3.connect(path)
            connection.execute(
                "UPDATE tags SET state = 'proposed_new' WHERE tag_id = ?",
                (trusted_tag_id,),
            )
            connection.execute(
                "UPDATE atom_tags SET origin = 'proposed_new' WHERE tag_id = ?",
                (trusted_tag_id,),
            )
            connection.execute(
                """
                INSERT INTO tags (
                    tag_id, namespace, canonical_text, display_text, level, state,
                    aliases_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "legacy-model-tag",
                    "project-a",
                    "unreviewed",
                    "Unreviewed",
                    "specific",
                    "proposed_new",
                    "[]",
                    "2026-09-02T00:00:00+00:00",
                ),
            )
            connection.execute(
                """
                INSERT INTO atom_tags (
                    atom_id, tag_id, weight_raw, confidence, origin,
                    evidence_sources_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    model_target.atom_ids[0],
                    "legacy-model-tag",
                    1.0,
                    0.8,
                    "proposed_new",
                    '["stub:model"]',
                    "2026-09-02T00:00:00+00:00",
                    "2026-09-02T00:00:00+00:00",
                ),
            )
            connection.execute("PRAGMA user_version = 0")
            connection.commit()
            connection.close()

            with SQLiteRepository(path) as reopened:
                self.assertEqual(
                    [tag.canonical_text for tag in reopened.list_tags("project-a")],
                    ["trusted"],
                )
                self.assertIs(
                    reopened.atom_tags_for(trusted.atom_ids[0])[0].origin,
                    TagOrigin.EXPLICIT,
                )
                candidates = reopened.list_tag_candidates(
                    namespace="project-a", state=TagCandidateState.PROPOSED
                )
                self.assertEqual(
                    [candidate.normalized_text for candidate in candidates],
                    ["unreviewed"],
                )
                self.assertEqual(reopened.atom_tags_for(model_target.atom_ids[0]), ())


if __name__ == "__main__":
    unittest.main()

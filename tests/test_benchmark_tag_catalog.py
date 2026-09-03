from __future__ import annotations

import unittest

from data_retrieval.benchmarks import BenchmarkTagCatalogResolver
from data_retrieval.domain.models import TagCandidateState
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.tagging.proposals import TagProposal


class FixedProposer:
    evidence_source = "fixture:benchmark-tags"
    proposal_version = "fixture-v1"

    def __init__(self, proposals: dict[str, tuple[TagProposal, ...]]) -> None:
        self.proposals = proposals

    def propose_tags(self, *, text, namespace, existing_tags):
        return self.proposals[text]


class BenchmarkTagCatalogResolverTests(unittest.TestCase):
    def test_promotes_strong_exact_family_merges_duplicates_and_rejects_weak(self) -> None:
        repository = InMemoryRepository()
        ingestion = IngestService(repository)
        first = ingestion.ingest_text(
            namespace="benchmark:case-1",
            source="first",
            text="PostgreSQL stores records.",
        )
        second = ingestion.ingest_text(
            namespace="benchmark:case-1",
            source="second",
            text="Postgres is durable.",
        )
        proposer = FixedProposer(
            {
                "PostgreSQL stores records.": (
                    TagProposal("PostgreSQL", 0.91),
                    TagProposal("Vague", 0.40),
                ),
                "Postgres is durable.": (TagProposal("postgresql", 0.72),),
            }
        )
        enrichment = TagEnrichmentService(repository, proposer)
        enrichment.enrich_document(first.document_id)
        enrichment.enrich_document(second.document_id)

        result = BenchmarkTagCatalogResolver(
            repository, minimum_confidence=0.65
        ).resolve_namespace("benchmark:case-1")

        self.assertEqual(result.proposed_count, 3)
        self.assertEqual(result.promoted_count, 1)
        self.assertEqual(result.merged_count, 1)
        self.assertEqual(result.rejected_count, 1)
        self.assertEqual(
            [tag.canonical_text for tag in repository.list_tags("benchmark:case-1")],
            ["postgresql"],
        )
        self.assertEqual(len(repository.atom_tags_for(first.atom_ids[0])), 1)
        self.assertEqual(len(repository.atom_tags_for(second.atom_ids[0])), 1)
        self.assertEqual(
            len(
                repository.list_tag_candidates(
                    namespace="benchmark:case-1", state=TagCandidateState.PROPOSED
                )
            ),
            0,
        )


if __name__ == "__main__":
    unittest.main()

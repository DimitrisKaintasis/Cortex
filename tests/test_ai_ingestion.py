import unittest

from data_retrieval.ingestion.chunker import TextChunker
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.tagging.proposals import TagProposal


class StubTagProposer:
    evidence_source = "stub:test-model"
    proposal_version = "stub-v1"

    def __init__(self, responses: tuple[tuple[TagProposal, ...], ...]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str, tuple[str, ...]]] = []

    def propose_tags(
        self,
        *,
        text: str,
        namespace: str,
        existing_tags: tuple[str, ...],
    ) -> tuple[TagProposal, ...]:
        self.calls.append((text, namespace, existing_tags))
        return self.responses[len(self.calls) - 1]


class FailingTagProposer:
    evidence_source = "stub:failing"
    proposal_version = "stub-v1"

    def propose_tags(
        self,
        *,
        text: str,
        namespace: str,
        existing_tags: tuple[str, ...],
    ) -> tuple[TagProposal, ...]:
        raise RuntimeError("provider unavailable")


class StubBatchTagProposer(StubTagProposer):
    def __init__(self, responses: tuple[tuple[TagProposal, ...], ...]) -> None:
        super().__init__(responses)
        self.batch_calls: list[tuple[tuple[str, ...], str, tuple[str, ...]]] = []

    def propose_tags_batch(
        self,
        *,
        texts: tuple[str, ...],
        namespace: str,
        existing_tags: tuple[str, ...],
    ) -> tuple[tuple[TagProposal, ...], ...]:
        self.batch_calls.append((texts, namespace, existing_tags))
        return self.responses


class AiIngestionTests(unittest.TestCase):
    def test_batch_provider_classifies_one_document_in_one_call(self) -> None:
        repository = InMemoryRepository()
        proposer = StubBatchTagProposer(
            responses=(
                (TagProposal("database", 0.9),),
                (TagProposal("remote inference", 0.8),),
            )
        )
        result = IngestService(
            repository, chunker=TextChunker(max_chars=100, overlap_chars=0)
        ).ingest_text(
            namespace="project-a",
            source="batch.txt",
            text=(
                "PostgreSQL stores canonical atoms and weighted tags.\n\n"
                "The Mac provides remote model inference over an SSH tunnel."
            ),
        )

        TagEnrichmentService(repository, proposer).enrich_document(result.document_id)

        self.assertEqual(len(proposer.batch_calls), 1)
        self.assertEqual(len(proposer.batch_calls[0][0]), 2)
        self.assertEqual(len(repository.list_tags("project-a")), 2)

    def test_model_tags_are_atom_specific_normalized_and_traceable(self) -> None:
        repository = InMemoryRepository()
        proposer = StubTagProposer(
            responses=(
                (
                    TagProposal("Data_Retrieval", 0.7),
                    TagProposal("data-retrieval", 0.9),
                    TagProposal("Architecture", 0.95),
                ),
                (TagProposal("Remote Inference", 0.8),),
            )
        )
        ingestion = IngestService(
            repository,
            chunker=TextChunker(max_chars=100, overlap_chars=0),
        )

        result = ingestion.ingest_text(
            namespace="project-a",
            source="notes/mac.txt",
            text=(
                "The laptop owns canonical storage and ingestion state.\n\n"
                "The Mac provides remote model inference when requested."
            ),
            explicit_tags=("Architecture",),
            metadata={"actor_type": "human"},
        )
        enrichment = TagEnrichmentService(repository, proposer).enrich_document(result.document_id)

        self.assertEqual(len(result.atom_ids), 2)
        self.assertFalse(enrichment.idempotent)
        self.assertEqual(len(proposer.calls), 2)
        self.assertEqual(
            [tag.canonical_text for tag in repository.list_tags("project-a")],
            ["architecture", "data retrieval", "remote inference"],
        )

        first_edges = repository.atom_tags_for(result.atom_ids[0])
        second_edges = repository.atom_tags_for(result.atom_ids[1])
        tags_by_id = {tag.tag_id: tag for tag in repository.list_tags("project-a")}
        first = {tags_by_id[edge.tag_id].canonical_text: edge for edge in first_edges}
        second = {tags_by_id[edge.tag_id].canonical_text: edge for edge in second_edges}

        self.assertEqual(set(first), {"architecture", "data retrieval"})
        self.assertEqual(set(second), {"architecture", "remote inference"})
        self.assertEqual(first["data retrieval"].confidence, 0.9)
        self.assertEqual(first["data retrieval"].evidence_sources, ("stub:test-model",))
        self.assertEqual(
            first["architecture"].evidence_sources,
            ("explicit", "stub:test-model"),
        )
        self.assertEqual(second["architecture"].evidence_sources, ("explicit",))
        self.assertEqual(repository.get_atom(result.atom_ids[0]).metadata["actor_type"], "human")
        self.assertEqual(
            repository.get_atom(result.atom_ids[0]).metadata["source"],
            "notes/mac.txt",
        )

    def test_provider_failure_does_not_remove_raw_ingestion(self) -> None:
        repository = InMemoryRepository()
        result = IngestService(repository).ingest_text(
            namespace="project-a",
            source="failure.txt",
            text="This must remain durably available.",
            explicit_tags=("source",),
        )

        with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
            TagEnrichmentService(repository, FailingTagProposer()).enrich_document(
                result.document_id
            )

        self.assertEqual(repository.document_count, 1)
        self.assertEqual(repository.atom_count, 1)
        self.assertEqual(repository.tag_count, 1)
        self.assertIsNotNone(repository.get_document(result.document_id))

    def test_idempotent_enrichment_does_not_call_model_again(self) -> None:
        repository = InMemoryRepository()
        proposer = StubTagProposer(responses=((TagProposal("Architecture", 0.8),),))
        ingestion = IngestService(repository)
        ingested = ingestion.ingest_text(
            namespace="project-a",
            source="same.txt",
            text="The exact same content.",
        )
        service = TagEnrichmentService(repository, proposer)
        first = service.enrich_document(ingested.document_id)
        second = service.enrich_document(ingested.document_id)

        reingested = ingestion.ingest_text(
            namespace="project-a", source="same.txt", text="The exact same content."
        )

        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        self.assertTrue(reingested.idempotent)
        self.assertEqual(len(proposer.calls), 1)

    def test_empty_enrichment_is_also_idempotent(self) -> None:
        repository = InMemoryRepository()
        proposer = StubTagProposer(responses=((),))
        ingested = IngestService(repository).ingest_text(
            namespace="project-a",
            source="no-tags.txt",
            text="A valid atom that needs no model tags.",
        )
        service = TagEnrichmentService(repository, proposer)

        first = service.enrich_document(ingested.document_id)
        second = service.enrich_document(ingested.document_id)

        self.assertFalse(first.idempotent)
        self.assertTrue(second.idempotent)
        self.assertEqual(len(proposer.calls), 1)


if __name__ == "__main__":
    unittest.main()

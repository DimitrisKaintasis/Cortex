import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data_retrieval.mem0 import Mem0Entity, Mem0EntityRelationship, Mem0ProcessResult
from data_retrieval.services.document_pipeline import (
    DocumentPipelineService,
    TemporalPipelineRequest,
)
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.proposals import TagProposal
from data_retrieval.temporal import TemporalBridge


class StubTagProposer:
    evidence_source = "stub:pipeline"
    proposal_version = "v1"

    def propose_tags(
        self,
        *,
        text: str,
        namespace: str,
        existing_tags: tuple[str, ...],
    ) -> tuple[TagProposal, ...]:
        del text, namespace, existing_tags
        return (TagProposal("retrieval architecture", 0.9),)


class FailingTagProposer:
    evidence_source = "stub:failing"
    proposal_version = "v1"

    def propose_tags(
        self,
        *,
        text: str,
        namespace: str,
        existing_tags: tuple[str, ...],
    ) -> tuple[TagProposal, ...]:
        del text, namespace, existing_tags
        raise RuntimeError("provider unavailable")


class StubEmbedder:
    provider = "stub"
    model = "pipeline-vectors"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple((float(index + 1), 1.0) for index, _ in enumerate(texts))

    def embed_query(self, text: str) -> tuple[float, ...]:
        del text
        return (1.0, 1.0)


class StubMem0Processor:
    profile_id = "stub-mem0-v1"

    def add(
        self,
        messages: tuple[dict[str, str], ...],
        *,
        user_id: str,
        run_id: str | None,
        metadata: dict[str, object],
    ) -> Mem0ProcessResult:
        del messages, user_id, run_id
        source_atom_ids = tuple(str(value) for value in metadata["source_atom_ids"])
        support = (source_atom_ids[0],)
        return Mem0ProcessResult(
            entities=(
                Mem0Entity("entity-alice", "Alice", support),
                Mem0Entity("entity-openai", "OpenAI", support),
            ),
            relationships=(
                Mem0EntityRelationship(
                    "relationship-1",
                    "entity-alice",
                    "entity-openai",
                    "works_at",
                    support,
                    confidence=0.95,
                ),
            ),
        )


class DocumentPipelineTests(unittest.TestCase):
    def test_temporal_mem0_and_vector_stages_share_the_canonical_namespace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "timeline.txt"
            source.write_text("Alice works at OpenAI.", encoding="utf-8")
            start = datetime(2026, 9, 4, tzinfo=UTC)
            with SQLiteRepository(root / "data.sqlite3") as repository:
                report = DocumentPipelineService(
                    repository,
                    embedder=StubEmbedder(),
                    temporal_bridge=TemporalBridge(),
                    mem0_processor=StubMem0Processor(),
                ).run(
                    path=source,
                    namespace="project-a",
                    source="timeline",
                    occurred_at=start + timedelta(hours=1),
                    metadata={"timeline_id": "main"},
                    temporal=TemporalPipelineRequest(
                        timeline_id="main",
                        timezone_name="UTC",
                        range_start=start,
                        range_end=start + timedelta(days=1),
                        state_path=root / "temporal.sqlite3",
                    ),
                )

            stages = {stage.name: stage for stage in report.stages}
            self.assertEqual(report.status, "completed")
            self.assertEqual(stages["temporal_projection"].status, "completed")
            self.assertGreater(stages["temporal_projection"].details["coverage_count"], 0)
            self.assertEqual(stages["mem0_bootstrap"].status, "completed")
            self.assertEqual(stages["mem0_bootstrap"].details["entities_returned"], 2)
            self.assertEqual(stages["embedding_enrichment"].status, "completed")
            self.assertEqual(stages["mem0_vector_calibration"].status, "completed")
            self.assertTrue(stages["weight_audit"].details["passed"])

    def test_pipeline_runs_enabled_stages_and_reuses_derived_data(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "notes.txt"
            source.write_text(
                "Atoms preserve source evidence.\n\nTags support explainable retrieval.",
                encoding="utf-8",
            )
            checkpoint_statuses: list[str] = []
            with SQLiteRepository(root / "data.sqlite3") as repository:
                service = DocumentPipelineService(
                    repository,
                    tag_proposer=StubTagProposer(),
                    embedder=StubEmbedder(),
                    ingest_batch_size=1,
                )
                first = service.run(
                    path=source,
                    namespace="project-a",
                    source="notes",
                    checkpoint=lambda report: checkpoint_statuses.append(report.status),
                )
                second = service.run(
                    path=source,
                    namespace="project-a",
                    source="notes",
                )

                atoms = repository.get_atoms_for_document(first.document_id or "")
                embeddings = repository.get_embeddings(
                    atom_ids=tuple(atom.atom_id for atom in atoms),
                    provider="stub",
                    model="pipeline-vectors",
                )

            self.assertEqual(first.status, "completed")
            self.assertEqual(first.run_id, second.run_id)
            self.assertEqual(
                tuple((stage.name, stage.status) for stage in first.stages),
                (
                    ("canonical_ingestion", "completed"),
                    ("tag_enrichment", "completed"),
                    ("temporal_projection", "skipped"),
                    ("mem0_bootstrap", "skipped"),
                    ("embedding_enrichment", "completed"),
                    ("mem0_vector_calibration", "skipped"),
                    ("weight_audit", "completed"),
                ),
            )
            self.assertFalse(first.stages[0].details["idempotent"])
            self.assertTrue(second.stages[0].details["idempotent"])
            self.assertTrue(second.stages[1].details["idempotent"])
            self.assertEqual(first.stages[4].details["embedded_count"], len(atoms))
            self.assertEqual(second.stages[4].details["reused_count"], len(atoms))
            self.assertEqual(len(embeddings), len(atoms))
            self.assertEqual(checkpoint_statuses[0], "running")
            self.assertEqual(checkpoint_statuses[-1], "completed")

    def test_failed_stage_is_checkpointed_and_can_be_retried(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "notes.txt"
            source.write_text("Retry-safe evidence.", encoding="utf-8")
            checkpoints = []
            with SQLiteRepository(root / "data.sqlite3") as repository:
                with self.assertRaisesRegex(RuntimeError, "provider unavailable"):
                    DocumentPipelineService(
                        repository,
                        tag_proposer=FailingTagProposer(),
                    ).run(
                        path=source,
                        namespace="project-a",
                        source="notes",
                        checkpoint=checkpoints.append,
                    )

                recovered = DocumentPipelineService(
                    repository,
                    tag_proposer=StubTagProposer(),
                ).run(
                    path=source,
                    namespace="project-a",
                    source="notes",
                )

            failed = checkpoints[-1]
            self.assertEqual(failed.status, "failed")
            self.assertEqual(failed.stages[-1].name, "tag_enrichment")
            self.assertEqual(failed.stages[-1].status, "failed")
            self.assertEqual(failed.stages[-1].error_type, "RuntimeError")
            self.assertEqual(recovered.status, "completed")
            self.assertTrue(recovered.stages[0].details["idempotent"])


if __name__ == "__main__":
    unittest.main()

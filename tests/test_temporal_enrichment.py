import tempfile
import unittest
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

from data_retrieval.domain.models import AtomKind, AtomLinkRelation
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.temporal_enrichment import TemporalEnrichmentService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.temporal import TemporalBridge


class TemporalEnrichmentServiceTests(unittest.TestCase):
    def test_selects_stored_source_atoms_and_persists_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            db_path = Path(directory) / "data.sqlite3"
            state_path = Path(directory) / "temporal.sqlite3"
            start = datetime(2026, 8, 19, 12, tzinfo=UTC)
            with SQLiteRepository(db_path) as repository:
                ingestion = IngestService(repository)
                included = ingestion.ingest_text(
                    namespace="project-a",
                    source="included",
                    text="Decision: retain the two-stage ingestion boundary.",
                    occurred_at=datetime(2026, 8, 19, 15, tzinfo=timezone(timedelta(hours=3))),
                    metadata={"timeline_id": "main"},
                )
                ingestion.ingest_text(
                    namespace="project-a",
                    source="other-timeline",
                    text="This belongs to another timeline.",
                    occurred_at=start + timedelta(minutes=20),
                    metadata={"timeline_id": "other"},
                )
                ingestion.ingest_text(
                    namespace="project-a",
                    source="untimed",
                    text="This has no event time.",
                )

                result = TemporalEnrichmentService(repository, TemporalBridge()).enrich_range(
                    namespace="project-a",
                    timeline_id="main",
                    timezone_name="UTC",
                    range_start=start,
                    range_end=start + timedelta(hours=1),
                    state_path=state_path,
                )

                summaries = repository.get_atoms_for_document(result.bundle.document.document_id)
                self.assertEqual(len(summaries), 5)
                self.assertTrue(all(atom.kind is AtomKind.TEMPORAL_SUMMARY for atom in summaries))
                six_hour = next(
                    atom for atom in summaries if atom.metadata["granularity"] == "six_hour"
                )
                sources = {
                    link.to_atom_id
                    for link in repository.get_atom_links(six_hour.atom_id)
                    if link.relation is AtomLinkRelation.SUMMARIZES
                }
                self.assertEqual(sources, set(included.atom_ids))
                self.assertIn("model_config_hash", six_hour.metadata)
                self.assertIn("context_selections", six_hour.metadata)


if __name__ == "__main__":
    unittest.main()

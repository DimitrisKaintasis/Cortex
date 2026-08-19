import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path

from data_retrieval.domain.models import AtomKind, AtomLinkRelation
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.temporal import TemporalBridge


class TemporalBridgeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryRepository()
        self.ingestion = IngestService(self.repository)
        self.start = datetime(2026, 8, 19, 12, tzinfo=UTC)
        first = self.ingestion.ingest_text(
            namespace="project-a",
            source="conversation/1",
            text="Decision: atoms remain canonical evidence.",
            occurred_at=self.start + timedelta(minutes=10),
        )
        second = self.ingestion.ingest_text(
            namespace="project-a",
            source="conversation/2",
            text="Action: generate temporal summaries as derived atoms.",
            occurred_at=self.start + timedelta(minutes=20),
        )
        self.source_atom_ids = (*first.atom_ids, *second.atom_ids)
        self.source_atoms = tuple(
            self.repository.get_atom(atom_id) for atom_id in self.source_atom_ids
        )

    def test_projects_hierarchy_to_atoms_with_auditable_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "temporal-state.sqlite3"
            projection = TemporalBridge().project(
                namespace="project-a",
                timeline_id="main",
                atoms=self.source_atoms,
                timezone_name="UTC",
                range_start=self.start,
                range_end=self.start + timedelta(hours=1),
                state_path=state_path,
            )
            self.repository.persist_ingestion(projection.bundle)

            self.assertEqual(
                projection.summary_counts,
                {"six_hour": 1, "day": 1, "week": 1, "month": 1, "year": 1},
            )
            self.assertEqual(len(projection.bundle.atoms), 5)
            self.assertTrue(
                all(atom.kind is AtomKind.TEMPORAL_SUMMARY for atom in projection.bundle.atoms)
            )

            six_hour = next(
                atom
                for atom in projection.bundle.atoms
                if atom.metadata["granularity"] == "six_hour"
            )
            self.assertEqual(six_hour.metadata["scope_type"], "channel")
            self.assertEqual(six_hour.metadata["timeline_id"], "main")
            self.assertIn("context_selections", six_hour.metadata)
            self.assertIn("source_event_ids", six_hour.metadata)
            self.assertIn("model_config_hash", six_hour.metadata)
            source_links = [
                link
                for link in self.repository.get_atom_links(six_hour.atom_id)
                if link.relation is AtomLinkRelation.SUMMARIZES
            ]
            self.assertEqual({link.to_atom_id for link in source_links}, set(self.source_atom_ids))
            self.assertGreater(self.repository.atom_link_count, len(source_links))

    def test_maps_temporal_source_metadata_without_losing_recorded_time(self) -> None:
        recorded_at = self.start - timedelta(hours=1)
        result = self.ingestion.ingest_text(
            namespace="project-a",
            source="conversation/metadata",
            text="A threaded message with actor metadata.",
            occurred_at=self.start + timedelta(minutes=30),
            metadata={
                "timeline_id": "main",
                "recorded_at": recorded_at.isoformat(),
                "event_type": "message",
                "actor_id": "user-1",
                "actor_display_name": "Dimitris",
                "actor_type": "human",
                "actor_resolution_source": "export",
                "actor_resolution_timestamp": recorded_at.isoformat(),
                "actor_is_active": True,
                "thread_root_id": "thread-1",
                "is_thread_reply": True,
                "parent_event_id": "event-0",
            },
        )
        atom = self.repository.get_atom(result.atom_ids[0])

        event = TemporalBridge()._to_events(
            namespace="project-a",
            timeline_id="main",
            atoms=(atom,),
            range_start=self.start,
            range_end=self.start + timedelta(hours=1),
        )[0]

        self.assertEqual(event.recorded_at, recorded_at)
        self.assertEqual(event.actor_display_name, "Dimitris")
        self.assertEqual(event.actor_resolution_source, "export")
        self.assertEqual(event.thread_root_id, "thread-1")
        self.assertTrue(event.is_thread_reply)

    def test_reuses_temporal_state_for_an_identical_projection(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            state_path = Path(directory) / "temporal-state.sqlite3"
            bridge = TemporalBridge()
            first = bridge.project(
                namespace="project-a",
                timeline_id="main",
                atoms=self.source_atoms,
                timezone_name="UTC",
                range_start=self.start,
                range_end=self.start + timedelta(hours=1),
                state_path=state_path,
            )
            second = bridge.project(
                namespace="project-a",
                timeline_id="main",
                atoms=self.source_atoms,
                timezone_name="UTC",
                range_start=self.start,
                range_end=self.start + timedelta(hours=1),
                state_path=state_path,
            )

            self.assertEqual(
                tuple(atom.atom_id for atom in first.bundle.atoms),
                tuple(atom.atom_id for atom in second.bundle.atoms),
            )
            self.assertTrue(all(level["generated"] == 0 for level in second.generation.values()))
            self.assertTrue(all(level["reused"] == 1 for level in second.generation.values()))

    def test_rejects_atoms_without_an_occurrence_time(self) -> None:
        result = self.ingestion.ingest_text(
            namespace="project-a",
            source="untimed-note",
            text="This note has no source occurrence time.",
        )
        atom = self.repository.get_atom(result.atom_ids[0])

        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, "has no occurred_at"):
                TemporalBridge().project(
                    namespace="project-a",
                    timeline_id="main",
                    atoms=(atom,),
                    timezone_name="UTC",
                    range_start=self.start,
                    range_end=self.start + timedelta(hours=1),
                    state_path=Path(directory) / "temporal-state.sqlite3",
                )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import unittest
from dataclasses import fields, replace
from datetime import datetime
from pathlib import Path

from data_retrieval.connectors import (
    ConnectorCapability,
    ContextPack,
    RecordPayload,
    Scope,
    SyncBatch,
    SyncItemType,
    TemporalQueryMode,
    Visibility,
    context_pack_from_mapping,
    outcome_from_mapping,
    query_from_mapping,
    source_from_mapping,
    sync_batch_acknowledgement_from_mapping,
    sync_batch_from_mapping,
    sync_commit_acknowledgement_from_mapping,
)


class ConnectorContractFixtureTests(unittest.TestCase):
    def setUp(self) -> None:
        fixture_root = Path(__file__).parents[1] / "evals"
        self.devui = self._load(fixture_root / "connector_contract_devui_v1.json")
        self.slack = self._load(fixture_root / "connector_contract_slack_v1.json")

    @staticmethod
    def _load(path: Path) -> dict[str, object]:
        value = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(value, dict)
        return value

    def _parse(self, fixture: dict[str, object]) -> tuple[object, ...]:
        registration = source_from_mapping(fixture["registration"])
        batch = sync_batch_from_mapping(fixture["sync_batch"])
        batch_acknowledgement = sync_batch_acknowledgement_from_mapping(
            fixture["sync_batch_acknowledgement"]
        )
        commit_acknowledgement = sync_commit_acknowledgement_from_mapping(
            fixture["sync_commit_acknowledgement"]
        )
        query = query_from_mapping(fixture["query"])
        context = context_pack_from_mapping(fixture["context_pack"])
        outcome = outcome_from_mapping(fixture["outcome"])
        return (
            registration,
            batch,
            batch_acknowledgement,
            commit_acknowledgement,
            query,
            context,
            outcome,
        )

    def test_devui_fixture_uses_generic_structured_records_and_round_trips_identity(self) -> None:
        registration, batch, batch_ack, commit_ack, query, context, outcome = self._parse(
            self.devui
        )

        self.assertIn(ConnectorCapability.SOURCE_SYNC, registration.capabilities)
        self.assertEqual(batch.batch_id, batch_ack.batch_id)
        self.assertTrue(batch_ack.complete)
        self.assertEqual(batch.run.proposed_cursor, commit_ack.committed_cursor)
        self.assertEqual(len(batch.records), 3)
        self.assertEqual(
            {relation.relation_type for relation in batch.relations},
            {"belongs_to", "calls"},
        )
        self.assertEqual(query.scope, registration.owner_scope)
        self.assertEqual(context.query_request_id, query.request_id)
        self.assertEqual(outcome.retrieval_id, context.retrieval_id)
        self.assertEqual(
            context.items[0].record.external_id,
            "function:src/auth/service.py:login",
        )
        self.assertEqual(
            set(outcome.used_evidence_ids),
            {item.evidence_id for item in context.items},
        )

    def test_slack_fixture_represents_edits_threads_tombstones_and_cursors(self) -> None:
        registration, batch, batch_ack, commit_ack, query, context, outcome = self._parse(
            self.slack
        )

        self.assertIn(ConnectorCapability.CHANGE_OBSERVATION, registration.capabilities)
        self.assertEqual(batch.sequence, batch_ack.sequence)
        self.assertEqual(batch.run.request_id, batch_ack.run_request_id)
        self.assertEqual(batch.run.proposed_cursor, commit_ack.committed_cursor)
        self.assertEqual(batch.run.previous_cursor, "event-page:17")
        self.assertEqual(batch.run.proposed_cursor, "event-page:18")
        versions = [
            record.ref
            for record in batch.records
            if record.ref.external_id == "channel:C-AUTH:message:100.0001"
        ]
        self.assertEqual(len(versions), 2)
        self.assertEqual(versions[0].object_key, versions[1].object_key)
        self.assertNotEqual(versions[0].version_key, versions[1].version_key)
        self.assertEqual(batch.relations[0].relation_type, "reply_to")
        self.assertEqual(len(batch.tombstones), 1)
        self.assertEqual(batch.tombstones[0].reason, "source_deleted")
        self.assertEqual(context.items[0].record.external_version, "edited:100.0005")
        self.assertEqual(query.scope, context.items[0].scope)
        self.assertEqual(outcome.retrieval_id, context.retrieval_id)

    def test_public_contract_does_not_require_internal_cortex_vocabulary(self) -> None:
        public_types = (
            Scope,
            SyncBatch,
            ContextPack,
        )
        forbidden = {"atom", "tag", "weight", "calibration", "repository", "namespace"}

        for contract_type in public_types:
            field_names = {field.name for field in fields(contract_type)}
            self.assertTrue(forbidden.isdisjoint(field_names))

    def test_duplicate_record_version_is_rejected_but_distinct_versions_are_allowed(self) -> None:
        batch = sync_batch_from_mapping(self.slack["sync_batch"])
        first = batch.records[0]

        with self.assertRaisesRegex(ValueError, "duplicate record versions"):
            SyncBatch(
                batch_id="duplicate-test",
                sequence=0,
                run=batch.run,
                records=(first, first),
            )

        SyncBatch(
            batch_id="distinct-test",
            sequence=0,
            run=batch.run,
            records=(batch.records[0], batch.records[1]),
        )

    def test_one_batch_cannot_upsert_and_tombstone_the_same_object(self) -> None:
        batch = sync_batch_from_mapping(self.slack["sync_batch"])
        conflicting = replace(
            batch.tombstones[0],
            record=replace(
                batch.tombstones[0].record,
                external_id=batch.records[0].ref.external_id,
            ),
        )

        with self.assertRaisesRegex(ValueError, "upsert and tombstone"):
            SyncBatch(
                batch_id="conflict-test",
                sequence=0,
                run=batch.run,
                records=(batch.records[0],),
                tombstones=(conflicting,),
            )

    def test_scope_rejects_missing_visibility_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "project visibility requires project_id"):
            Scope(visibility=Visibility.PROJECT, organization_id="org:acme")

    def test_payload_requires_one_inline_or_reference_value(self) -> None:
        with self.assertRaisesRegex(ValueError, "exactly one"):
            RecordPayload()
        with self.assertRaisesRegex(ValueError, "exactly one"):
            RecordPayload(inline="text", reference_uri="https://example.invalid/blob")
        with self.assertRaisesRegex(ValueError, "requires content_hash"):
            RecordPayload(reference_uri="https://example.invalid/blob")

    def test_naive_fixture_timestamp_is_rejected(self) -> None:
        batch = sync_batch_from_mapping(self.devui["sync_batch"])

        with self.assertRaisesRegex(ValueError, "timezone"):
            replace(batch.run, started_at=datetime(2026, 9, 18, 9, 0, 0))

    def test_empty_context_requires_an_abstention_reason(self) -> None:
        with self.assertRaisesRegex(ValueError, "requires abstention_reason"):
            ContextPack(
                retrieval_id="retrieval:empty",
                query_request_id="query:empty",
                items=(),
                low_confidence=True,
            )

        pack = ContextPack(
            retrieval_id="retrieval:empty",
            query_request_id="query:empty",
            items=(),
            low_confidence=True,
            abstention_reason="No permitted evidence met the relevance threshold.",
        )
        self.assertEqual(pack.items, ())

    def test_invalid_zero_top_k_is_not_replaced_by_default(self) -> None:
        query = dict(self.devui["query"])  # type: ignore[arg-type]
        query["top_k"] = 0

        with self.assertRaisesRegex(ValueError, "top_k must be positive"):
            query_from_mapping(query)

    def test_temporal_modes_require_unambiguous_boundaries(self) -> None:
        as_of_query = dict(self.devui["query"])  # type: ignore[arg-type]
        as_of_query["temporal_mode"] = "as_of"

        with self.assertRaisesRegex(ValueError, "requires as_of"):
            query_from_mapping(as_of_query)

        as_of_query["as_of"] = "2026-09-18T08:59:00+00:00"
        parsed_as_of = query_from_mapping(as_of_query)
        self.assertEqual(parsed_as_of.temporal_mode, TemporalQueryMode.AS_OF)
        self.assertIsNotNone(parsed_as_of.as_of)

        range_query = dict(self.devui["query"])  # type: ignore[arg-type]
        range_query["temporal_mode"] = "range"
        range_query["range_start"] = "2026-09-18T08:00:00+00:00"

        with self.assertRaisesRegex(ValueError, "provided together"):
            query_from_mapping(range_query)

        range_query["range_end"] = "2026-09-18T09:00:00+00:00"
        parsed_range = query_from_mapping(range_query)
        self.assertEqual(parsed_range.temporal_mode, TemporalQueryMode.RANGE)

    def test_unknown_fields_fail_instead_of_falling_back_to_defaults(self) -> None:
        query = dict(self.devui["query"])  # type: ignore[arg-type]
        query["topK"] = query.pop("top_k")

        with self.assertRaisesRegex(ValueError, "unknown fields: topK"):
            query_from_mapping(query)

    def test_metadata_is_json_safe_and_deeply_immutable(self) -> None:
        registration = source_from_mapping(self.devui["registration"])
        frozen = replace(
            registration,
            metadata={"nested": {"values": [1, 2]}},
        )

        with self.assertRaises(TypeError):
            frozen.metadata["new"] = "value"  # type: ignore[index]
        nested = frozen.metadata["nested"]
        self.assertEqual(nested, {"values": (1, 2)})

        with self.assertRaisesRegex(ValueError, "JSON-compatible"):
            replace(registration, metadata={"invalid": datetime.now})  # type: ignore[dict-item]

    def test_batch_failures_are_item_specific_and_block_completion(self) -> None:
        acknowledgement = dict(self.slack["sync_batch_acknowledgement"])  # type: ignore[arg-type]
        acknowledgement["accepted_records"] = 2
        acknowledgement["failures"] = [
            {
                "item_type": "record",
                "item_id": "channel:C-AUTH:message:100.0001",
                "item_version": "edited:100.0005",
                "code": "scope_denied",
                "message": "The record scope is not permitted for this source.",
                "retryable": False,
            }
        ]

        parsed = sync_batch_acknowledgement_from_mapping(acknowledgement)

        self.assertFalse(parsed.complete)
        self.assertEqual(parsed.failures[0].item_type, SyncItemType.RECORD)


if __name__ == "__main__":
    unittest.main()

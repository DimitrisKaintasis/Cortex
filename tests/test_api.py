from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from data_retrieval.api import LocalApiConfig, create_app
from data_retrieval.connectors.codec import source_from_mapping, sync_run_from_mapping
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.proposals import TagProposal


class StubTagProposer:
    evidence_source = "stub:api-review"
    proposal_version = "v1"

    def propose_tags(
        self,
        *,
        text: str,
        namespace: str,
        existing_tags: tuple[str, ...],
    ) -> tuple[TagProposal, ...]:
        del text, namespace, existing_tags
        return (TagProposal("retrieval architecture", 0.91),)


class LocalApiTests(unittest.TestCase):
    def test_connector_openapi_exposes_contract_shapes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(LocalApiConfig(database_path=Path(directory) / "data.sqlite3"))
            with TestClient(app) as client:
                document = client.get("/openapi.json").json()

        source_operation = document["paths"]["/v1/sources"]["post"]
        source_schema = source_operation["requestBody"]["content"]["application/json"][
            "schema"
        ]
        batch_operation = document["paths"]["/v1/sync-runs/{run_request_id}/batches"][
            "post"
        ]
        batch_schema = batch_operation["requestBody"]["content"]["application/json"][
            "schema"
        ]

        self.assertIn("source", source_schema["properties"])
        self.assertIn("owner_scope", source_schema["properties"])
        self.assertIn("batch_id", batch_schema["properties"])
        self.assertIn("records", batch_schema["properties"])

    def test_connector_source_batch_replay_and_commit_flow(self) -> None:
        fixture_path = (
            Path(__file__).parents[1] / "evals" / "connector_contract_devui_v1.json"
        )
        fixture = json.loads(fixture_path.read_text(encoding="utf-8"))
        registration = fixture["registration"]
        batch = fixture["sync_batch"]
        query = fixture["query"]

        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "data.sqlite3"
            with TestClient(create_app(LocalApiConfig(database_path=database))) as client:
                registered = client.post("/v1/sources", json=registration)
                fetched = client.get(
                    "/v1/sources/devui/project:cortex",
                )
                accepted = client.post(
                    "/v1/sync-runs/devui-sync:scan-42/batches",
                    json=batch,
                )
                replayed = client.post(
                    "/v1/sync-runs/devui-sync:scan-42/batches",
                    json=batch,
                )
                run = client.get("/v1/sync-runs/devui-sync:scan-42")
                committed = client.post(
                    "/v1/sync-runs/devui-sync:scan-42:commit",
                    json={"request_id": "devui-sync:scan-42:commit"},
                )
                committed_replay = client.post(
                    "/v1/sync-runs/devui-sync:scan-42:commit",
                    json={"request_id": "devui-sync:scan-42:commit"},
                )
                context = client.post("/v1/queries", json=query)
                context_replay = client.post("/v1/queries", json=query)
                outcome_payload = {
                    "request_id": "devui-outcome:api-test",
                    "retrieval_id": context.json()["retrieval_id"],
                    "used_evidence_ids": [context.json()["items"][0]["evidence_id"]],
                    "outcome": "positive",
                    "occurred_at": "2026-09-18T12:31:00+03:00",
                    "reason": "Used by the API integration test.",
                }
                outcome = client.post("/v1/outcomes", json=outcome_payload)
                outcome_replay = client.post("/v1/outcomes", json=outcome_payload)

        self.assertEqual(registered.status_code, 201)
        self.assertEqual(
            source_from_mapping(registered.json()), source_from_mapping(registration)
        )
        self.assertEqual(source_from_mapping(fetched.json()), source_from_mapping(registration))
        self.assertEqual(accepted.status_code, 200)
        self.assertEqual(accepted.json(), replayed.json())
        self.assertEqual(accepted.json()["accepted_records"], len(batch["records"]))
        self.assertEqual(accepted.json()["accepted_relations"], len(batch["relations"]))
        self.assertEqual(
            sync_run_from_mapping(run.json()), sync_run_from_mapping(batch["run"])
        )
        self.assertEqual(committed.status_code, 200)
        self.assertEqual(committed.json(), committed_replay.json())
        self.assertEqual(committed.json()["committed_cursor"], "scan:42")
        self.assertEqual(context.status_code, 200)
        self.assertEqual(context.json(), context_replay.json())
        self.assertTrue(context.json()["items"])
        self.assertNotIn("atom_id", context.json()["items"][0])
        self.assertIn("external_id", context.json()["items"][0]["record"])
        self.assertEqual(outcome.status_code, 200)
        self.assertEqual(outcome.json(), outcome_replay.json())

    def test_ingestion_retrieval_explanation_and_feedback_flow(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "data.sqlite3"
            with TestClient(create_app(LocalApiConfig(database_path=database))) as client:
                health = client.get("/health")
                created = client.post(
                    "/v1/documents",
                    json={
                        "namespace": "project-a",
                        "source": "architecture-note",
                        "text": "PostgreSQL stores the canonical retrieval graph.",
                        "tags": ["architecture"],
                    },
                )
                repeated = client.post(
                    "/v1/documents",
                    json={
                        "namespace": "project-a",
                        "source": "architecture-note",
                        "text": "PostgreSQL stores the canonical retrieval graph.",
                        "tags": ["architecture"],
                    },
                )
                namespaces = client.get("/v1/namespaces")
                retrieved = client.post(
                    "/v1/retrievals",
                    json={
                        "namespace": "project-a",
                        "query": "Where is the retrieval graph stored?",
                        "tags": ["architecture"],
                        "top_k": 3,
                    },
                )

                item = retrieved.json()["items"][0]
                feedback = client.post(
                    "/v1/feedback",
                    json={
                        "retrieval_id": retrieved.json()["retrieval_id"],
                        "selected_atom_ids": [item["atom_id"]],
                        "outcome": "positive",
                        "reason": "used in the answer",
                    },
                )

            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.json()["storage"], "sqlite")
            self.assertEqual(created.status_code, 201)
            self.assertFalse(created.json()["idempotent"])
            self.assertTrue(repeated.json()["idempotent"])
            self.assertEqual(namespaces.json()["namespaces"], ["project-a"])
            self.assertEqual(retrieved.status_code, 200)
            self.assertIn("PostgreSQL", item["content"])
            self.assertGreater(item["score"]["final"], 0.0)
            self.assertTrue(item["score"]["evidence"])
            self.assertIn("channel_weights", retrieved.json()["diagnostics"])
            self.assertEqual(feedback.status_code, 200)
            self.assertEqual(feedback.json()["credited_atom_ids"], [item["atom_id"]])
            self.assertEqual(feedback.json()["atom_tag_updates"], 1)

    def test_candidate_review_includes_evidence_and_activates_promoted_tag(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "data.sqlite3"
            with SQLiteRepository(database) as repository:
                ingested = IngestService(repository).ingest_text(
                    namespace="project-a",
                    source="design-note",
                    text="The system uses weighted tags for retrieval.",
                )
                enriched = TagEnrichmentService(
                    repository, StubTagProposer()
                ).enrich_document(ingested.document_id)
            candidate_id = enriched.candidate_ids[0]

            with TestClient(create_app(LocalApiConfig(database_path=database))) as client:
                listed = client.get(
                    "/v1/tag-candidates",
                    params={"namespace": "project-a", "state": "proposed"},
                )
                resolved = client.post(
                    f"/v1/tag-candidates/{candidate_id}/resolution",
                    json={"action": "promote"},
                )
                listed_after = client.get(
                    "/v1/tag-candidates",
                    params={"namespace": "project-a", "state": "canonicalized"},
                )

            candidate = listed.json()["candidates"][0]
            self.assertEqual(listed.status_code, 200)
            self.assertEqual(candidate["candidate_id"], candidate_id)
            self.assertIn("weighted tags", candidate["evidence"]["content"])
            self.assertEqual(candidate["evidence"]["source"], "design-note")
            self.assertEqual(resolved.status_code, 200)
            self.assertEqual(resolved.json()["state"], "canonicalized")
            self.assertTrue(resolved.json()["atom_tag_activated"])
            self.assertEqual(listed_after.json()["count"], 1)

    def test_invalid_feedback_is_a_bounded_client_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(
                LocalApiConfig(database_path=Path(directory) / "data.sqlite3")
            )
            with TestClient(app) as client:
                response = client.post(
                    "/v1/feedback",
                    json={
                        "retrieval_id": "missing",
                        "selected_atom_ids": ["atom-1"],
                        "outcome": "positive",
                    },
                )

            self.assertEqual(response.status_code, 400)
            self.assertEqual(response.json()["detail"], "unknown retrieval_id: missing")


if __name__ == "__main__":
    unittest.main()

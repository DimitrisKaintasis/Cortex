from __future__ import annotations

import unittest
from types import SimpleNamespace

from data_retrieval.mem0.provenance import Mem0ProvenanceAdapter


class _FakeLlm:
    def __init__(self) -> None:
        self.messages = []

    def generate_response(self, *, messages, tools):
        self.messages = messages
        self.tools = tools
        return {
            "tool_calls": [
                {
                    "name": "establish_relationships",
                    "arguments": {
                        "entities": [
                            {
                                "source": "Alice",
                                "relationship": "works at",
                                "destination": "OpenAI",
                                "evidence_source_ids": ["atom-1"],
                            }
                        ]
                    },
                }
            ]
        }


class _FakeGraph:
    def __init__(self) -> None:
        self.llm = _FakeLlm()
        self.llm_provider = "openai"
        self.config = SimpleNamespace(
            graph_store=SimpleNamespace(custom_prompt=None)
        )
        self.plain_data = ""
        self.stored_relations = []

    def _retrieve_nodes_from_data(self, data, filters):
        self.plain_data = data
        return {"alice": "person", "openai": "organization"}

    def _remove_spaces_from_entities(self, values):
        return [
            {
                "source": value["source"].casefold().replace(" ", "_"),
                "relationship": value["relationship"].casefold().replace(" ", "_"),
                "destination": value["destination"].casefold().replace(" ", "_"),
            }
            for value in values
        ]

    def _search_graph_db(self, *, node_list, filters):
        return []

    def _get_delete_entities_from_search_output(self, search_output, data, filters):
        return []

    def _delete_entities(self, values, filters):
        return []

    def _add_entities(self, values, filters, entity_type_map):
        self.stored_relations = values
        return values


class _FakeMemory:
    enable_graph = True

    def __init__(self) -> None:
        self.graph = _FakeGraph()
        self.original_graph_calls = 0

    def _add_to_graph(self, messages, filters):
        self.original_graph_calls += 1
        return {"original": True}

    def add(self, messages, **options):
        filters = {"user_id": options.get("user_id")}
        return {
            "results": [{"id": "normal-mem0-fact"}],
            "relations": self._add_to_graph(messages, filters),
        }


class Mem0ProvenanceAdapterTests(unittest.TestCase):
    def test_extension_preserves_plain_entity_input_and_strips_storage_markers(self) -> None:
        memory = _FakeMemory()
        adapter = Mem0ProvenanceAdapter(memory)

        response = adapter.add(
            ({"role": "user", "content": "Alice works at OpenAI."},),
            source_atom_ids=("atom-1",),
            user_id="project-a",
            infer=True,
        )

        self.assertEqual(memory.graph.plain_data, "Alice works at OpenAI.")
        self.assertNotIn("EVIDENCE_SOURCE_ID", memory.graph.plain_data)
        self.assertIn("EVIDENCE_SOURCE_ID: atom-1", memory.graph.llm.messages[1]["content"])
        self.assertEqual(
            memory.graph.stored_relations,
            [
                {
                    "source": "alice",
                    "relationship": "works_at",
                    "destination": "openai",
                }
            ],
        )
        relation = response["relations"]["provenance_relationships"][0]
        self.assertTrue(relation["provenance_valid"])
        self.assertEqual(relation["evidence_source_ids"], ["atom-1"])
        self.assertEqual(response["results"], [{"id": "normal-mem0-fact"}])

    def test_calls_outside_adapter_keep_original_mem0_graph_path(self) -> None:
        memory = _FakeMemory()
        Mem0ProvenanceAdapter(memory)

        result = memory._add_to_graph(
            [{"role": "user", "content": "Normal Mem0 call"}], {"user_id": "u"}
        )

        self.assertEqual(result, {"original": True})
        self.assertEqual(memory.original_graph_calls, 1)


if __name__ == "__main__":
    unittest.main()

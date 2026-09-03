from __future__ import annotations

import unittest
from types import SimpleNamespace

from data_retrieval.mem0.ollama_compat import configure_ollama_llm


class _Client:
    def __init__(self) -> None:
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(
            message=SimpleNamespace(
                content="",
                tool_calls=[
                    SimpleNamespace(
                        function=SimpleNamespace(
                            name="extract_entities",
                            arguments={
                                "entities": [
                                    {"entity": "Alice", "entity_type": "person"}
                                ]
                            },
                        )
                    )
                ],
            )
        )


class _OllamaLikeLlm:
    __module__ = "mem0.llms.ollama"

    def __init__(self) -> None:
        self.client = _Client()
        self.config = SimpleNamespace(
            model="test-model", temperature=0.1, max_tokens=100, top_p=0.9
        )
        self.original_calls = 0

    def generate_response(
        self, messages, response_format=None, tools=None, tool_choice="auto", **kwargs
    ):
        self.original_calls += 1
        return "normal-response"


class Mem0OllamaCompatibilityTests(unittest.TestCase):
    def test_tool_schema_is_forwarded_and_native_call_is_normalized(self) -> None:
        llm = _OllamaLikeLlm()
        configure_ollama_llm(llm, enable_tools=True)
        tool = {"type": "function", "function": {"name": "extract_entities"}}

        response = llm.generate_response(
            [{"role": "user", "content": "Alice"}], tools=[tool]
        )

        self.assertEqual(response["tool_calls"][0]["name"], "extract_entities")
        self.assertEqual(
            response["tool_calls"][0]["arguments"]["entities"][0]["entity"],
            "Alice",
        )
        self.assertEqual(llm.client.calls[0]["tools"], [tool])
        self.assertFalse(llm.client.calls[0]["think"])

    def test_non_tool_calls_keep_mem0_behavior(self) -> None:
        llm = _OllamaLikeLlm()
        configure_ollama_llm(llm, enable_tools=True)

        result = llm.generate_response([{"role": "user", "content": "hello"}])

        self.assertEqual(result, "normal-response")
        self.assertEqual(llm.original_calls, 1)


if __name__ == "__main__":
    unittest.main()

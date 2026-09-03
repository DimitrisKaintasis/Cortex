from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any


def configure_ollama_llm(llm: Any, *, enable_tools: bool) -> None:
    """Patch one Mem0 1.x Ollama instance without changing its core prompts.

    Mem0 1.0.1 accepts ``tools`` in its Ollama method signature but does not send
    them to Ollama and discards native tool calls. The graph pipeline consequently
    returns no entities. This instance-local shim only repairs that transport gap.
    """

    if llm.__class__.__module__ != "mem0.llms.ollama":
        return
    client = getattr(llm, "client", None)
    chat = getattr(client, "chat", None)
    if not callable(chat):
        return

    def chat_without_thinking(*args: Any, **kwargs: Any) -> Any:
        kwargs.setdefault("think", False)
        return chat(*args, **kwargs)

    client.chat = chat_without_thinking
    if not enable_tools:
        return

    original_generate = llm.generate_response

    def generate_response(
        messages: list[dict[str, str]],
        response_format: Any = None,
        tools: list[dict[str, Any]] | None = None,
        tool_choice: str = "auto",
        **kwargs: Any,
    ) -> Any:
        if not tools:
            return original_generate(
                messages,
                response_format=response_format,
                tools=tools,
                tool_choice=tool_choice,
                **kwargs,
            )
        config = llm.config
        options = {
            "temperature": config.temperature,
            "num_predict": config.max_tokens,
            "top_p": config.top_p,
        }
        response = client.chat(
            model=config.model,
            messages=messages,
            tools=tools,
            options=options,
            think=False,
        )
        return _normalized_tool_response(response)

    llm.generate_response = generate_response


def _normalized_tool_response(response: Any) -> dict[str, Any]:
    message = response.get("message", {}) if isinstance(response, Mapping) else response.message
    if isinstance(message, Mapping):
        content = str(message.get("content") or "")
        calls = message.get("tool_calls") or ()
    else:
        content = str(getattr(message, "content", "") or "")
        calls = getattr(message, "tool_calls", None) or ()
    normalized: list[dict[str, Any]] = []
    for call in calls:
        function = call.get("function", {}) if isinstance(call, Mapping) else call.function
        if isinstance(function, Mapping):
            name = str(function.get("name") or "")
            arguments = function.get("arguments", {})
        else:
            name = str(getattr(function, "name", "") or "")
            arguments = getattr(function, "arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError:
                arguments = {}
        if name and isinstance(arguments, Mapping):
            normalized.append({"name": name, "arguments": dict(arguments)})
    return {"content": content, "tool_calls": normalized}

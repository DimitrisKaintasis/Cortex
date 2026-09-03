from __future__ import annotations

from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from threading import RLock
from typing import Any

PROVENANCE_FIELD = "provenance_relationships"
PROVENANCE_SCHEMA_VERSION = "mem0-evidence-provenance-v1"


@dataclass(frozen=True, slots=True)
class _ProvenanceContext:
    source_atom_ids: tuple[str, ...]


class Mem0ProvenanceAdapter:
    """Instance-local, opt-in provenance extension for Mem0 graph ingestion.

    Normal ``Memory.add`` behavior is preserved. During calls made through this
    adapter, only graph relationship extraction receives stable evidence markers.
    The markers are stripped before Mem0 stores its normal entity triples.
    """

    def __init__(self, memory: Any) -> None:
        if not bool(getattr(memory, "enable_graph", False)):
            raise ValueError("Mem0 provenance requires an enabled graph store")
        graph = getattr(memory, "graph", None)
        required = (
            "_retrieve_nodes_from_data",
            "_search_graph_db",
            "_get_delete_entities_from_search_output",
            "_delete_entities",
            "_add_entities",
            "_remove_spaces_from_entities",
        )
        missing = tuple(name for name in required if not callable(getattr(graph, name, None)))
        if missing:
            raise ValueError(
                "Unsupported Mem0 graph API for the provenance extension: " + ", ".join(missing)
            )
        self.memory = memory
        self.graph = graph
        self._lock = RLock()
        self._active: _ProvenanceContext | None = None
        self._original_add_to_graph = memory._add_to_graph
        # This changes only this Memory instance, never the installed Mem0 package.
        memory._add_to_graph = self._add_to_graph

    def add(
        self,
        messages: Sequence[Mapping[str, str]],
        *,
        source_atom_ids: tuple[str, ...],
        **options: Any,
    ) -> Any:
        if len(messages) != len(source_atom_ids):
            raise ValueError("Mem0 messages and source_atom_ids must have equal lengths")
        if len(set(source_atom_ids)) != len(source_atom_ids) or any(
            not value.strip() for value in source_atom_ids
        ):
            raise ValueError("source_atom_ids must be non-empty and unique")
        with self._lock:
            if self._active is not None:
                raise RuntimeError("overlapping Mem0 provenance calls are not supported")
            self._active = _ProvenanceContext(source_atom_ids=source_atom_ids)
            try:
                return self.memory.add(list(messages), **options)
            finally:
                self._active = None

    def _add_to_graph(self, messages: Sequence[Mapping[str, str]], filters: dict[str, Any]) -> Any:
        context = self._active
        if context is None:
            return self._original_add_to_graph(messages, filters)
        if filters.get("user_id") is None:
            filters["user_id"] = "user"
        plain_parts: list[str] = []
        anchored_parts: list[str] = []
        allowed_ids: list[str] = []
        for message, atom_id in zip(messages, context.source_atom_ids, strict=True):
            if "content" not in message or message.get("role") == "system":
                continue
            content = str(message["content"])
            plain_parts.append(content)
            anchored_parts.append(f"[EVIDENCE_SOURCE_ID: {atom_id}]\n{content}")
            allowed_ids.append(atom_id)
        return self._graph_add(
            plain_data="\n".join(plain_parts),
            anchored_data="\n\n".join(anchored_parts),
            filters=filters,
            allowed_ids=tuple(allowed_ids),
        )

    def _graph_add(
        self,
        *,
        plain_data: str,
        anchored_data: str,
        filters: dict[str, Any],
        allowed_ids: tuple[str, ...],
    ) -> dict[str, Any]:
        entity_type_map = self.graph._retrieve_nodes_from_data(plain_data, filters)
        raw_relations = self._extract_relations(
            anchored_data=anchored_data,
            filters=filters,
            entity_type_map=entity_type_map,
        )
        storage_relations: list[dict[str, str]] = []
        provenance_relations: list[dict[str, Any]] = []
        allowed = set(allowed_ids)
        for relation in raw_relations:
            storage = {
                "source": str(relation.get("source", "")),
                "relationship": str(relation.get("relationship", "")),
                "destination": str(relation.get("destination", "")),
            }
            if all(storage.values()):
                normalized = self.graph._remove_spaces_from_entities([storage])
                if normalized:
                    storage_relations.append(normalized[0])
                    provenance_relations.append(
                        {
                            **normalized[0],
                            "evidence_source_ids": _clean_ids(
                                relation.get("evidence_source_ids"), allowed
                            ),
                            "provenance_valid": _provenance_valid(relation, allowed),
                            "provenance_schema": PROVENANCE_SCHEMA_VERSION,
                        }
                    )

        search_output = self.graph._search_graph_db(
            node_list=list(entity_type_map.keys()), filters=filters
        )
        to_be_deleted = self.graph._get_delete_entities_from_search_output(
            search_output, plain_data, filters
        )
        deleted_entities = self.graph._delete_entities(to_be_deleted, filters)
        added_entities = self.graph._add_entities(storage_relations, filters, entity_type_map)
        return {
            "deleted_entities": deleted_entities,
            "added_entities": added_entities,
            PROVENANCE_FIELD: provenance_relations,
        }

    def _extract_relations(
        self,
        *,
        anchored_data: str,
        filters: Mapping[str, Any],
        entity_type_map: Mapping[str, str],
    ) -> tuple[dict[str, Any], ...]:
        from mem0.graphs.tools import RELATIONS_STRUCT_TOOL, RELATIONS_TOOL
        from mem0.graphs.utils import EXTRACT_RELATIONS_PROMPT

        user_identity = f"user_id: {filters['user_id']}"
        if filters.get("agent_id"):
            user_identity += f", agent_id: {filters['agent_id']}"
        if filters.get("run_id"):
            user_identity += f", run_id: {filters['run_id']}"
        system_content = EXTRACT_RELATIONS_PROMPT.replace("USER_ID", user_identity)
        custom_prompt = getattr(self.graph.config.graph_store, "custom_prompt", None)
        if custom_prompt:
            system_content = system_content.replace("CUSTOM_PROMPT", f"4. {custom_prompt}")
            user_content = anchored_data
        else:
            user_content = (
                f"List of entities: {list(entity_type_map.keys())}.\n\nText: {anchored_data}"
            )
        system_content += (
            "\n\nEvery text block begins with an EVIDENCE_SOURCE_ID marker. For each "
            "relationship, copy only the exact marker IDs for the blocks that support "
            "that relationship into evidence_source_ids. Never invent or transform an ID."
        )
        base_tool = (
            RELATIONS_STRUCT_TOOL
            if self.graph.llm_provider in {"azure_openai_structured", "openai_structured"}
            else RELATIONS_TOOL
        )
        tool = _provenance_tool(base_tool)
        response = self.graph.llm.generate_response(
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": user_content},
            ],
            tools=[tool],
        )
        if not isinstance(response, Mapping):
            return ()
        calls = response.get("tool_calls")
        if not isinstance(calls, list | tuple) or not calls:
            return ()
        arguments = calls[0].get("arguments", {}) if isinstance(calls[0], Mapping) else {}
        entities = arguments.get("entities", ()) if isinstance(arguments, Mapping) else ()
        return tuple(dict(item) for item in entities if isinstance(item, Mapping))


def _provenance_tool(base_tool: Mapping[str, Any]) -> dict[str, Any]:
    tool = deepcopy(dict(base_tool))
    function = tool["function"]
    item_schema = function["parameters"]["properties"]["entities"]["items"]
    properties = item_schema["properties"]
    description = (
        "Exact EVIDENCE_SOURCE_ID values copied from the marked input blocks; "
        "return at least one value and never invent IDs."
    )
    properties["evidence_source_ids"] = {
        "type": "array",
        "items": {"type": "string"},
        "minItems": 1,
        "description": description,
    }
    item_schema["required"] = [
        *item_schema.get("required", ()),
        "evidence_source_ids",
    ]
    return tool


def _raw_ids(value: Any) -> tuple[str, ...]:
    if not isinstance(value, list | tuple):
        return ()
    return tuple(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


def _clean_ids(value: Any, allowed: set[str]) -> list[str]:
    return [item for item in _raw_ids(value) if item in allowed]


def _provenance_valid(item: Mapping[str, Any], allowed: set[str]) -> bool:
    values = _raw_ids(item.get("evidence_source_ids"))
    return bool(values) and set(values).issubset(allowed)

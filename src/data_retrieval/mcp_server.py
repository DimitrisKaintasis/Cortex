from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from pydantic import BaseModel, ConfigDict

from data_retrieval.connectors.codec import (
    context_pack_to_mapping,
    evidence_result_to_mapping,
    outcome_to_mapping,
)
from data_retrieval.connectors.contracts import (
    ContributionPolicy,
    Outcome,
    OutcomeValue,
    Query,
    Scope,
    TemporalQueryMode,
    Visibility,
)
from data_retrieval.local_runtime import LocalStorageConfig
from data_retrieval.services.connector_access import ConnectorAccessService

READ_ONLY = types.ToolAnnotations(
    read_only_hint=True,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)
OUTCOME_WRITE = types.ToolAnnotations(
    read_only_hint=False,
    destructive_hint=False,
    idempotent_hint=True,
    open_world_hint=False,
)


class ScopeInput(BaseModel):
    """Public Cortex scope. It routes local retrieval but does not grant authorization."""

    model_config = ConfigDict(extra="forbid")

    visibility: Visibility
    organization_id: str | None = None
    project_id: str | None = None
    user_id: str | None = None
    device_id: str | None = None
    contribution_policy: ContributionPolicy = ContributionPolicy.PRIVATE

    def to_contract(self) -> Scope:
        return Scope(
            visibility=self.visibility,
            organization_id=self.organization_id,
            project_id=self.project_id,
            user_id=self.user_id,
            device_id=self.device_id,
            contribution_policy=self.contribution_policy,
        )


@dataclass(frozen=True, slots=True)
class LocalMcpConfig(LocalStorageConfig):
    """Local-user-trust-boundary configuration for the stdio MCP adapter."""


def create_mcp_server(config: LocalMcpConfig | None = None) -> MCPServer[None]:
    settings = config or LocalMcpConfig()
    server: MCPServer[None] = MCPServer(
        "Cortex Local",
        version="0.1.0",
        instructions=(
            "Search Cortex evidence, build bounded context, inspect evidence returned by a "
            "retrieval, explain public scores, and report attributable outcomes. This local "
            "server does not expose source synchronization or administration tools."
        ),
    )

    def run_query(request: Query) -> dict[str, object]:
        try:
            with settings.open_repository() as repository:
                context = ConnectorAccessService(repository).query(request)
            return context_pack_to_mapping(context)
        except ValueError as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="cortex_search",
        title="Search Cortex evidence",
        description=(
            "Search evidence in one explicit Cortex scope. Returns a ContextPack with a "
            "retrieval ID, source-owned record identities, and opaque evidence IDs."
        ),
        annotations=READ_ONLY,
    )
    def cortex_search(
        request_id: str,
        query: str,
        scope: ScopeInput,
        top_k: int = 10,
    ) -> dict[str, object]:
        try:
            return run_query(
                Query(
                    request_id=request_id,
                    query=query,
                    scope=scope.to_contract(),
                    top_k=top_k,
                )
            )
        except ValueError as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="cortex_build_context",
        title="Build a bounded Cortex context pack",
        description=(
            "Retrieve a bounded context pack for an explicit scope, temporal mode, and token "
            "budget. Replaying the same request ID with changed input is rejected."
        ),
        annotations=READ_ONLY,
    )
    def cortex_build_context(
        request_id: str,
        query: str,
        scope: ScopeInput,
        top_k: int = 10,
        budget_tokens: int | None = None,
        timeline_id: str | None = None,
        temporal_mode: TemporalQueryMode = TemporalQueryMode.AUTO,
        as_of: datetime | None = None,
        range_start: datetime | None = None,
        range_end: datetime | None = None,
        reference_time: datetime | None = None,
    ) -> dict[str, object]:
        try:
            return run_query(
                Query(
                    request_id=request_id,
                    query=query,
                    scope=scope.to_contract(),
                    top_k=top_k,
                    budget_tokens=budget_tokens,
                    timeline_id=timeline_id,
                    temporal_mode=temporal_mode,
                    as_of=as_of,
                    range_start=range_start,
                    range_end=range_end,
                    reference_time=reference_time,
                )
            )
        except ValueError as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="cortex_get_evidence",
        title="Get evidence returned by a Cortex retrieval",
        description=(
            "Hydrate one opaque evidence ID only when it belongs to the named retrieval. "
            "An evidence ID alone is insufficient."
        ),
        annotations=READ_ONLY,
    )
    def cortex_get_evidence(
        retrieval_id: str, evidence_id: str
    ) -> dict[str, object]:
        try:
            with settings.open_repository() as repository:
                evidence = ConnectorAccessService(repository).get_evidence(
                    retrieval_id=retrieval_id, evidence_id=evidence_id
                )
            return evidence_result_to_mapping(evidence)
        except ValueError as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="cortex_explain_retrieval",
        title="Explain a Cortex retrieval",
        description=(
            "Return caller-safe scores, score evidence, source identity, and lineage evidence "
            "IDs for a prior retrieval. Internal atom IDs and unrestricted graph paths are hidden."
        ),
        annotations=READ_ONLY,
    )
    def cortex_explain_retrieval(retrieval_id: str) -> dict[str, object]:
        try:
            with settings.open_repository() as repository:
                return ConnectorAccessService(repository).explain_retrieval(retrieval_id)
        except ValueError as error:
            raise ToolError(str(error)) from error

    @server.tool(
        name="cortex_record_outcome",
        title="Record an attributable Cortex outcome",
        description=(
            "Record a positive or negative outcome using only evidence IDs returned by the "
            "named retrieval. The stable request ID makes exact replay idempotent."
        ),
        annotations=OUTCOME_WRITE,
    )
    def cortex_record_outcome(
        request_id: str,
        retrieval_id: str,
        used_evidence_ids: list[str],
        outcome: OutcomeValue,
        occurred_at: datetime,
        reason: str = "",
    ) -> dict[str, object]:
        try:
            report = Outcome(
                request_id=request_id,
                retrieval_id=retrieval_id,
                used_evidence_ids=tuple(used_evidence_ids),
                outcome=outcome,
                occurred_at=occurred_at,
                reason=reason,
            )
            with settings.open_repository() as repository:
                accepted = ConnectorAccessService(repository).report_outcome(report)
            return outcome_to_mapping(accepted)
        except ValueError as error:
            raise ToolError(str(error)) from error

    return server


def run_stdio_server(config: LocalMcpConfig | None = None) -> None:
    create_mcp_server(config).run(transport="stdio")

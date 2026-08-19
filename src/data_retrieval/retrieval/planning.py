from __future__ import annotations

from dataclasses import replace

from data_retrieval.retrieval.models import QueryPlan, TemporalMode


class QueryPlanner:
    """Resolve explicit temporal controls and conservative textual hints."""

    CURRENT_HINTS = frozenset({"current", "currently", "latest", "now", "status"})
    HISTORY_HINTS = frozenset(
        {"history", "historical", "changed", "changes", "previously", "timeline"}
    )

    def resolve(self, plan: QueryPlan) -> QueryPlan:
        if plan.temporal_mode is not TemporalMode.AUTO:
            return plan
        if plan.range_start is not None:
            return replace(plan, temporal_mode=TemporalMode.RANGE)
        if plan.as_of is not None:
            return replace(plan, temporal_mode=TemporalMode.AS_OF)
        tokens = {token.strip(".,?!:;()[]{}\"'").casefold() for token in plan.query.split()}
        if tokens.intersection(self.HISTORY_HINTS):
            return replace(plan, temporal_mode=TemporalMode.HISTORY)
        if tokens.intersection(self.CURRENT_HINTS):
            return replace(plan, temporal_mode=TemporalMode.CURRENT_STATE)
        return replace(plan, temporal_mode=TemporalMode.NONE)

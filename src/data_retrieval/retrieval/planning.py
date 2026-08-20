from __future__ import annotations

import re
from dataclasses import replace
from datetime import timedelta

from data_retrieval.domain.models import utc_now
from data_retrieval.retrieval.models import QueryPlan, TemporalMode


class QueryPlanner:
    """Resolve explicit temporal controls and conservative textual hints."""

    CURRENT_HINTS = frozenset({"current", "currently", "latest", "now", "status"})
    HISTORY_HINTS = frozenset(
        {"history", "historical", "changed", "changes", "previously", "timeline"}
    )
    NUMBER_WORDS = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
    }
    AGO_PATTERN = re.compile(
        r"\b(\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve)\s+"
        r"(hour|day|week|month|year)s?\s+ago\b",
        re.IGNORECASE,
    )

    def resolve(self, plan: QueryPlan) -> QueryPlan:
        if plan.temporal_mode is not TemporalMode.AUTO:
            return plan
        if plan.range_start is not None:
            return replace(plan, temporal_mode=TemporalMode.RANGE)
        if plan.as_of is not None:
            return replace(plan, temporal_mode=TemporalMode.AS_OF)
        relative = self._relative_as_of(plan.query, plan.reference_time or utc_now())
        if relative is not None:
            return replace(plan, temporal_mode=TemporalMode.AS_OF, as_of=relative)
        tokens = {token.strip(".,?!:;()[]{}\"'").casefold() for token in plan.query.split()}
        if tokens.intersection(self.HISTORY_HINTS):
            return replace(plan, temporal_mode=TemporalMode.HISTORY)
        if tokens.intersection(self.CURRENT_HINTS):
            return replace(plan, temporal_mode=TemporalMode.CURRENT_STATE)
        return replace(plan, temporal_mode=TemporalMode.NONE)

    def _relative_as_of(self, query: str, reference_time):
        lowered = query.casefold()
        match = self.AGO_PATTERN.search(lowered)
        if match:
            raw_count, unit = match.groups()
            count = int(raw_count) if raw_count.isdigit() else self.NUMBER_WORDS[raw_count]
            days = {"day": 1, "week": 7, "month": 30, "year": 365}
            delta = (
                timedelta(hours=count)
                if unit == "hour"
                else timedelta(days=count * days[unit])
            )
            return reference_time - delta
        if "yesterday" in lowered:
            return reference_time - timedelta(days=1)
        if "last week" in lowered:
            return reference_time - timedelta(days=7)
        return None

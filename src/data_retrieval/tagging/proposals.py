from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class TagProposal:
    """One untrusted tag suggestion returned by an enrichment provider."""

    text: str
    confidence: float

    def __post_init__(self) -> None:
        if not self.text.strip():
            raise ValueError("proposal text cannot be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("proposal confidence must be between 0 and 1")


class TagProposer(Protocol):
    """Replaceable boundary for optional AI-assisted ingestion."""

    @property
    def evidence_source(self) -> str: ...

    @property
    def proposal_version(self) -> str: ...

    def propose_tags(
        self,
        *,
        text: str,
        namespace: str,
        existing_tags: tuple[str, ...],
    ) -> tuple[TagProposal, ...]: ...

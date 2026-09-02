from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import Protocol

from data_retrieval.domain.models import Atom

TOKEN_PATTERN = re.compile(r"[^\W_]{2,}", re.UNICODE)
STOP_WORDS = frozenset(
    {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "be",
        "by",
        "for",
        "from",
        "has",
        "he",
        "in",
        "is",
        "it",
        "of",
        "on",
        "or",
        "she",
        "that",
        "the",
        "they",
        "this",
        "to",
        "was",
        "were",
        "will",
        "with",
    }
)


@dataclass(frozen=True, slots=True)
class SupportSelection:
    atom_ids: tuple[str, ...]
    method: str
    confidence: float
    scores: dict[str, float]


class SupportAligner(Protocol):
    """Select exact source candidates for one derived Mem0 fact."""

    @property
    def profile_id(self) -> str: ...

    def align(self, *, fact: str, atoms: tuple[Atom, ...]) -> SupportSelection: ...


class LexicalSupportAligner:
    """Bounded, deterministic support alignment that never links an entire batch by default."""

    profile_id = "mem0-lexical-support-v1"

    def __init__(
        self,
        *,
        maximum_support_atoms: int = 3,
        minimum_score: float = 0.12,
        relative_score: float = 0.70,
    ) -> None:
        if maximum_support_atoms <= 0:
            raise ValueError("maximum_support_atoms must be positive")
        if not 0.0 <= minimum_score <= 1.0:
            raise ValueError("minimum_score must be between 0 and 1")
        if not 0.0 <= relative_score <= 1.0:
            raise ValueError("relative_score must be between 0 and 1")
        self.maximum_support_atoms = maximum_support_atoms
        self.minimum_score = minimum_score
        self.relative_score = relative_score

    def align(self, *, fact: str, atoms: tuple[Atom, ...]) -> SupportSelection:
        if not atoms:
            return SupportSelection((), self.profile_id, 0.0, {})
        if len(atoms) == 1:
            return SupportSelection(
                (atoms[0].atom_id,), "single_source_atom", 1.0, {atoms[0].atom_id: 1.0}
            )

        fact_terms = self._terms(fact)
        if not fact_terms:
            return SupportSelection((), self.profile_id, 0.0, {})
        atom_terms = {atom.atom_id: self._terms(atom.content) for atom in atoms}
        document_frequency = Counter(
            term for terms in atom_terms.values() for term in set(terms)
        )
        weights = {
            term: math.log((len(atoms) + 1) / (document_frequency.get(term, 0) + 1)) + 1.0
            for term in set(fact_terms)
        }
        denominator = sum(weights.values()) or 1.0
        scores = {
            atom.atom_id: sum(
                weights[term]
                for term in set(fact_terms) & set(atom_terms[atom.atom_id])
            )
            / denominator
            for atom in atoms
        }
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        best_score = ranked[0][1]
        if best_score < self.minimum_score:
            return SupportSelection((), self.profile_id, best_score, scores)

        threshold = max(self.minimum_score, best_score * self.relative_score)
        selected = tuple(
            atom_id
            for atom_id, score in ranked
            if score >= threshold
        )[: self.maximum_support_atoms]
        return SupportSelection(selected, self.profile_id, best_score, scores)

    @staticmethod
    def _terms(text: str) -> tuple[str, ...]:
        return tuple(
            term
            for term in TOKEN_PATTERN.findall(text.casefold())
            if term not in STOP_WORDS
        )

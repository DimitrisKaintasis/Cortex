"""Tag normalization, canonicalization, and proposal boundaries."""

from data_retrieval.tagging.normalization import deduplicate_tags, normalize_tag
from data_retrieval.tagging.ollama import OllamaError, OllamaTagProposer
from data_retrieval.tagging.proposals import TagProposal, TagProposer

__all__ = [
    "OllamaError",
    "OllamaTagProposer",
    "TagProposal",
    "TagProposer",
    "deduplicate_tags",
    "normalize_tag",
]

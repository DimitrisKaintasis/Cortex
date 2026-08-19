from __future__ import annotations

from dataclasses import dataclass

from data_retrieval.retrieval.embedding import Embedder
from data_retrieval.retrieval.models import AtomEmbedding
from data_retrieval.storage.repository import Repository


@dataclass(frozen=True, slots=True)
class EmbeddingEnrichmentResult:
    namespace: str
    embedded_atom_ids: tuple[str, ...]
    reused_atom_ids: tuple[str, ...]


class EmbeddingEnrichmentService:
    """Persist replaceable semantic vectors without changing canonical atoms."""

    def __init__(
        self,
        repository: Repository,
        embedder: Embedder,
        batch_size: int = 32,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        self.repository = repository
        self.embedder = embedder
        self.batch_size = batch_size

    def enrich_namespace(self, namespace: str) -> EmbeddingEnrichmentResult:
        atoms = self.repository.list_atoms(namespace=namespace)
        atom_ids = tuple(atom.atom_id for atom in atoms)
        existing = self.repository.get_embeddings(
            atom_ids=atom_ids,
            provider=self.embedder.provider,
            model=self.embedder.model,
        )
        reusable_ids = {
            atom.atom_id
            for atom in atoms
            if atom.atom_id in existing and existing[atom.atom_id].content_hash == atom.content_hash
        }
        pending = [atom for atom in atoms if atom.atom_id not in reusable_ids]
        generated: list[AtomEmbedding] = []
        for offset in range(0, len(pending), self.batch_size):
            batch = pending[offset : offset + self.batch_size]
            vectors = self.embedder.embed_documents(tuple(atom.content for atom in batch))
            if len(vectors) != len(batch):
                raise ValueError("embedder returned the wrong number of vectors")
            generated.extend(
                AtomEmbedding(
                    atom_id=atom.atom_id,
                    provider=self.embedder.provider,
                    model=self.embedder.model,
                    dimensions=len(vector),
                    vector=vector,
                    content_hash=atom.content_hash,
                )
                for atom, vector in zip(batch, vectors, strict=True)
            )
        self.repository.upsert_embeddings(tuple(generated))
        return EmbeddingEnrichmentResult(
            namespace=namespace,
            embedded_atom_ids=tuple(item.atom_id for item in generated),
            reused_atom_ids=tuple(sorted(reusable_ids)),
        )

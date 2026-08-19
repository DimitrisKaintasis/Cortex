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
    embedded_count: int
    reused_count: int
    reported_ids_truncated: bool


class EmbeddingEnrichmentService:
    """Persist replaceable semantic vectors without changing canonical atoms."""

    def __init__(
        self,
        repository: Repository,
        embedder: Embedder,
        batch_size: int = 32,
        report_id_limit: int = 10_000,
    ) -> None:
        if batch_size <= 0:
            raise ValueError("batch_size must be positive")
        if report_id_limit < 0:
            raise ValueError("report_id_limit cannot be negative")
        self.repository = repository
        self.embedder = embedder
        self.batch_size = batch_size
        self.report_id_limit = report_id_limit

    def enrich_namespace(self, namespace: str) -> EmbeddingEnrichmentResult:
        embedded_ids: list[str] = []
        reused_ids: list[str] = []
        embedded_count = 0
        reused_count = 0
        for atoms in self.repository.iter_atoms(
            namespace=namespace, batch_size=self.batch_size
        ):
            atom_ids = tuple(atom.atom_id for atom in atoms)
            existing = self.repository.get_embeddings(
                atom_ids=atom_ids,
                provider=self.embedder.provider,
                model=self.embedder.model,
            )
            pending = []
            for atom in atoms:
                stored = existing.get(atom.atom_id)
                if stored is not None and stored.content_hash == atom.content_hash:
                    reused_count += 1
                    if len(reused_ids) < self.report_id_limit:
                        reused_ids.append(atom.atom_id)
                else:
                    pending.append(atom)
            if not pending:
                continue
            vectors = self.embedder.embed_documents(tuple(atom.content for atom in pending))
            if len(vectors) != len(pending):
                raise ValueError("embedder returned the wrong number of vectors")
            generated = tuple(
                AtomEmbedding(
                    atom_id=atom.atom_id,
                    provider=self.embedder.provider,
                    model=self.embedder.model,
                    dimensions=len(vector),
                    vector=vector,
                    content_hash=atom.content_hash,
                )
                for atom, vector in zip(pending, vectors, strict=True)
            )
            self.repository.upsert_embeddings(generated)
            embedded_count += len(generated)
            remaining = max(0, self.report_id_limit - len(embedded_ids))
            embedded_ids.extend(item.atom_id for item in generated[:remaining])
        return EmbeddingEnrichmentResult(
            namespace=namespace,
            embedded_atom_ids=tuple(embedded_ids),
            reused_atom_ids=tuple(reused_ids),
            embedded_count=embedded_count,
            reused_count=reused_count,
            reported_ids_truncated=(
                embedded_count > len(embedded_ids) or reused_count > len(reused_ids)
            ),
        )

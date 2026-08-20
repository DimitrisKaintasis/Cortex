from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import Atom, CalibrationSignal, CalibrationTarget
from data_retrieval.mem0.importer import Mem0ImportService, Mem0Record
from data_retrieval.storage.repository import Repository

MEM0_BOOTSTRAP_PROFILE = "mem0-bootstrap-v3"
DEFAULT_ATOM_BATCH_SIZE = 32
DEFAULT_MAX_BATCH_CHARS = 24_000
SAFE_ROLES = frozenset({"user", "assistant", "system"})


class Mem0Processor(Protocol):
    """Small boundary around the OSS Mem0 SDK, making the bridge testable."""

    def add(
        self,
        messages: tuple[dict[str, str], ...],
        *,
        user_id: str,
        run_id: str | None,
        metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]: ...


class Mem0PythonProcessor:
    """Use the current self-hosted Mem0 Python SDK without a core dependency."""

    def __init__(self, config: Mapping[str, Any] | None = None) -> None:
        try:
            from mem0 import Memory
        except ImportError as error:
            raise ValueError(
                "Mem0 is not installed; install the optional dependency with "
                "'pip install -e .[mem0]'"
            ) from error
        self._memory = Memory.from_config(dict(config)) if config else Memory()

    def add(
        self,
        messages: tuple[dict[str, str], ...],
        *,
        user_id: str,
        run_id: str | None,
        metadata: dict[str, Any],
    ) -> tuple[dict[str, Any], ...]:
        options: dict[str, Any] = {
            "user_id": user_id,
            "metadata": metadata,
            "infer": True,
        }
        if run_id is not None:
            options["run_id"] = run_id
        response = self._memory.add(list(messages), **options)
        if not isinstance(response, dict):
            raise ValueError("Mem0 add returned an unsupported response")
        raw_results = response.get("results", ())
        if not isinstance(raw_results, list | tuple):
            raise ValueError("Mem0 add response.results must be a list")
        return tuple(dict(item) for item in raw_results if isinstance(item, dict))


@dataclass(frozen=True, slots=True)
class Mem0BootstrapResult:
    namespace: str
    documents_examined: int
    batches_processed: int
    batches_resumed: int
    source_atoms_processed: int
    source_atoms_resumed: int
    memories_returned: int
    empty_batches: int
    memories_imported: int
    exact_duplicates: int
    semantic_duplicates: int
    source_lineage_links_created: int
    calibration_signals_created: int
    truncated: bool


class Mem0BootstrapService:
    """Distill existing native atoms through Mem0 and map the results back safely."""

    def __init__(
        self,
        repository: Repository,
        processor: Mem0Processor,
        *,
        importer: Mem0ImportService | None = None,
        atom_batch_size: int = DEFAULT_ATOM_BATCH_SIZE,
        max_batch_chars: int = DEFAULT_MAX_BATCH_CHARS,
        accept_empty: bool = False,
    ) -> None:
        if atom_batch_size <= 0:
            raise ValueError("atom_batch_size must be positive")
        if max_batch_chars <= 0:
            raise ValueError("max_batch_chars must be positive")
        self.repository = repository
        self.processor = processor
        self.importer = importer or Mem0ImportService(repository)
        self.atom_batch_size = atom_batch_size
        self.max_batch_chars = max_batch_chars
        self.accept_empty = accept_empty

    def run(
        self,
        *,
        namespace: str,
        user_id: str | None = None,
        max_documents: int | None = None,
    ) -> Mem0BootstrapResult:
        if not namespace.strip():
            raise ValueError("namespace cannot be empty")
        if max_documents is not None and max_documents <= 0:
            raise ValueError("max_documents must be positive")

        counters = Counter()
        documents_examined = 0
        truncated = False
        for document_ids in self.repository.iter_document_ids_chronological(
            namespace=namespace
        ):
            for document_id in document_ids:
                document = self.repository.get_document(document_id)
                if document is None or self._is_mem0_output(
                    source=document.source, metadata=document.metadata
                ):
                    continue
                if max_documents is not None and documents_examined >= max_documents:
                    truncated = True
                    break
                documents_examined += 1
                for atom_ids in self.repository.iter_atom_ids_for_document(
                    document_id=document_id, batch_size=self.atom_batch_size
                ):
                    atoms = self.repository.get_atoms(atom_ids)
                    for batch in self._batches(atoms):
                        self._process_batch(
                            namespace=namespace,
                            document_id=document_id,
                            atoms=batch,
                            user_id=user_id or f"data-retrieval:{namespace}",
                            counters=counters,
                        )
            if truncated:
                break

        return Mem0BootstrapResult(
            namespace=namespace,
            documents_examined=documents_examined,
            batches_processed=counters["batches_processed"],
            batches_resumed=counters["batches_resumed"],
            source_atoms_processed=counters["source_atoms_processed"],
            source_atoms_resumed=counters["source_atoms_resumed"],
            memories_returned=counters["memories_returned"],
            empty_batches=counters["empty_batches"],
            memories_imported=counters["memories_imported"],
            exact_duplicates=counters["exact_duplicates"],
            semantic_duplicates=counters["semantic_duplicates"],
            source_lineage_links_created=counters["source_lineage_links_created"],
            calibration_signals_created=counters["calibration_signals_created"],
            truncated=truncated,
        )

    def _process_batch(
        self,
        *,
        namespace: str,
        document_id: str,
        atoms: tuple[Atom, ...],
        user_id: str,
        counters: Counter[str],
    ) -> None:
        source_atom_ids = tuple(atom.atom_id for atom in atoms)
        batch_id = stable_id(
            "mem0-bootstrap-batch", namespace, document_id, *source_atom_ids
        )
        marker_ids = tuple(
            self._marker_id(namespace=namespace, batch_id=batch_id, atom_id=atom_id)
            for atom_id in source_atom_ids
        )
        if marker_ids and self.repository.get_calibration_signal_ids(marker_ids) == frozenset(
            marker_ids
        ):
            counters["batches_resumed"] += 1
            counters["source_atoms_resumed"] += len(atoms)
            return

        raw_results = self.processor.add(
            tuple(self._message(atom) for atom in atoms),
            user_id=user_id,
            run_id=None,
            metadata={
                "source_system": "data-retrieval",
                "source_namespace": namespace,
                "source_document_id": document_id,
                "source_atom_ids": list(source_atom_ids),
                "bootstrap_batch_id": batch_id,
                "bootstrap_profile": MEM0_BOOTSTRAP_PROFILE,
            },
        )
        records = self._records(
            raw_results,
            namespace=namespace,
            batch_id=batch_id,
            source_atom_ids=source_atom_ids,
            tags=self._inherited_tags(source_atom_ids),
            atoms=atoms,
        )
        counters["memories_returned"] += len(records)
        if records:
            imported = self.importer.import_records(namespace=namespace, records=records)
            counters["memories_imported"] += len(imported.imported_record_ids)
            counters["exact_duplicates"] += len(imported.exact_duplicate_record_ids)
            counters["semantic_duplicates"] += len(imported.semantic_duplicate_record_ids)
            counters["source_lineage_links_created"] += (
                imported.source_lineage_links_created
            )
            counters["calibration_signals_created"] += (
                imported.calibration_signals_created
            )
        else:
            counters["empty_batches"] += 1
            if not self.accept_empty:
                counters["batches_processed"] += 1
                counters["source_atoms_processed"] += len(atoms)
                return

        markers = tuple(
            CalibrationSignal(
                signal_id=marker_id,
                namespace=namespace,
                target_type=CalibrationTarget.ATOM,
                target_id=atom_id,
                signal_type="mem0_bootstrap_processed",
                value=1.0 if records else 0.0,
                confidence=1.0,
                multiplier=1.0,
                provider="mem0",
                profile_version=MEM0_BOOTSTRAP_PROFILE,
                source_reference=batch_id,
                metadata={"memories_returned": len(records)},
            )
            for marker_id, atom_id in zip(marker_ids, source_atom_ids, strict=True)
        )
        existing = self.repository.get_calibration_signal_ids(marker_ids)
        pending = tuple(marker for marker in markers if marker.signal_id not in existing)
        if pending:
            self.repository.apply_calibration_updates(
                signals=pending,
                atom_tags=(),
                atom_links=(),
                tag_relations=(),
            )
            counters["calibration_signals_created"] += len(pending)
        counters["batches_processed"] += 1
        counters["source_atoms_processed"] += len(atoms)

    def _batches(self, atoms: tuple[Atom, ...]) -> tuple[tuple[Atom, ...], ...]:
        batches: list[tuple[Atom, ...]] = []
        pending: list[Atom] = []
        pending_chars = 0
        for atom in sorted(atoms, key=lambda item: (item.position, item.atom_id)):
            atom_chars = len(atom.content)
            if pending and (
                len(pending) >= self.atom_batch_size
                or pending_chars + atom_chars > self.max_batch_chars
            ):
                batches.append(tuple(pending))
                pending = []
                pending_chars = 0
            pending.append(atom)
            pending_chars += atom_chars
        if pending:
            batches.append(tuple(pending))
        return tuple(batches)

    @staticmethod
    def _message(atom: Atom) -> dict[str, str]:
        raw_role = str(atom.metadata.get("role", "user")).casefold()
        role = raw_role if raw_role in SAFE_ROLES else "user"
        return {"role": role, "content": atom.content}

    def _inherited_tags(self, atom_ids: tuple[str, ...]) -> tuple[str, ...]:
        edges = self.repository.get_atom_tags_for_atoms(atom_ids)
        if not edges:
            return ()
        tag_counts = Counter(edge.tag_id for edge in edges)
        minimum_count = max(1, (len(atom_ids) + 1) // 2)
        selected_ids = tuple(
            tag_id
            for tag_id, _ in sorted(
                tag_counts.items(), key=lambda item: (-item[1], item[0])
            )
            if tag_counts[tag_id] >= minimum_count
        )[:12]
        tags_by_id = {tag.tag_id: tag for tag in self.repository.get_tags(selected_ids)}
        return tuple(
            tags_by_id[tag_id].display_text
            for tag_id in selected_ids
            if tag_id in tags_by_id
        )

    @staticmethod
    def _records(
        raw_results: tuple[dict[str, Any], ...],
        *,
        namespace: str,
        batch_id: str,
        source_atom_ids: tuple[str, ...],
        tags: tuple[str, ...],
        atoms: tuple[Atom, ...],
    ) -> tuple[Mem0Record, ...]:
        records: list[Mem0Record] = []
        occurred_values = [atom.occurred_at for atom in atoms if atom.occurred_at]
        occurred_at = max(occurred_values) if occurred_values else None
        for index, item in enumerate(raw_results):
            content = str(item.get("memory") or item.get("content") or "").strip()
            if not content:
                continue
            remote_id = str(item.get("id") or item.get("memory_id") or index)
            record_id = stable_id(
                "mem0-bootstrap-output",
                namespace,
                batch_id,
                remote_id,
                content_hash(content),
            )
            records.append(
                Mem0Record(
                    record_id=record_id,
                    content=content,
                    tags=tags,
                    occurred_at=occurred_at,
                    metadata={
                        "source_atom_ids": list(source_atom_ids),
                        "bootstrap_batch_id": batch_id,
                        "mem0_remote_id": remote_id,
                        "mem0_event": str(item.get("event") or "ADD"),
                    },
                )
            )
        return tuple(records)

    @staticmethod
    def _marker_id(*, namespace: str, batch_id: str, atom_id: str) -> str:
        return stable_id(
            "calibration", namespace, MEM0_BOOTSTRAP_PROFILE, batch_id, atom_id
        )

    @staticmethod
    def _is_mem0_output(*, source: str, metadata: Mapping[str, Any]) -> bool:
        return source.startswith("mem0:") or metadata.get("source_system") == "mem0"

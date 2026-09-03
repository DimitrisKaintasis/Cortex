from __future__ import annotations

import json
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Any, Protocol

from data_retrieval.core.identifiers import content_hash, stable_id
from data_retrieval.domain.models import Atom, AtomRole, CalibrationSignal, CalibrationTarget
from data_retrieval.mem0.entities import (
    Mem0EntityImportService,
    Mem0ProcessResult,
    normalize_mem0_response,
)
from data_retrieval.mem0.ollama_compat import configure_ollama_llm
from data_retrieval.mem0.provenance import Mem0ProvenanceAdapter
from data_retrieval.storage.repository import Repository

MEM0_BOOTSTRAP_PROFILE = "mem0-bootstrap-v5"
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
    ) -> Mem0ProcessResult: ...


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
        effective_config = dict(config or {})
        self.profile_id = self._profile_id(effective_config)
        self._memory = Memory.from_config(effective_config)
        if not bool(getattr(self._memory, "enable_graph", False)):
            raise ValueError(
                "Mem0 entity ingestion requires graph_store configuration; "
                "configure a graph backend supported by the installed Mem0 version"
            )
        configure_ollama_llm(self._memory.llm, enable_tools=False)
        configure_ollama_llm(self._memory.graph.llm, enable_tools=True)
        self._provenance = Mem0ProvenanceAdapter(self._memory)

    @staticmethod
    def _profile_id(config: Mapping[str, Any]) -> str:
        llm = config.get("llm")
        llm_mapping = llm if isinstance(llm, Mapping) else {}
        llm_config = llm_mapping.get("config")
        llm_config_mapping = llm_config if isinstance(llm_config, Mapping) else {}
        try:
            mem0_version = version("mem0ai")
        except PackageNotFoundError:
            mem0_version = "unknown"
        profile = {
            "bridge": MEM0_BOOTSTRAP_PROFILE,
            "mem0_version": mem0_version,
            "llm_provider": llm_mapping.get("provider", "default"),
            "llm_model": llm_config_mapping.get("model", "default"),
            "graph_provider": (
                config.get("graph_store", {}).get("provider", "disabled")
                if isinstance(config.get("graph_store"), Mapping)
                else "disabled"
            ),
            "cortex_import": "entity_graph_only",
            "mem0_infer": True,
            "ollama_think": False,
        }
        fingerprint = content_hash(
            json.dumps(profile, sort_keys=True, separators=(",", ":"), default=str)
        )[:16]
        return f"{MEM0_BOOTSTRAP_PROFILE}:{fingerprint}"

    def add(
        self,
        messages: tuple[dict[str, str], ...],
        *,
        user_id: str,
        run_id: str | None,
        metadata: dict[str, Any],
    ) -> Mem0ProcessResult:
        options: dict[str, Any] = {
            "user_id": user_id,
            "metadata": metadata,
            # Preserve Mem0's tested fact-memory and graph behavior. Cortex imports
            # only the provenance-bearing entity graph from the returned payload.
            "infer": True,
        }
        if run_id is not None:
            options["run_id"] = run_id
        source_atom_ids = tuple(str(value) for value in metadata.get("source_atom_ids", ()))
        response = self._provenance.add(messages, source_atom_ids=source_atom_ids, **options)
        if not isinstance(response, dict):
            raise ValueError("Mem0 add returned an unsupported response")
        return normalize_mem0_response(
            response,
            messages=messages,
            source_atom_ids=source_atom_ids,
            batch_id=str(metadata.get("bootstrap_batch_id", "")),
        )


@dataclass(frozen=True, slots=True)
class Mem0BootstrapResult:
    namespace: str
    documents_examined: int
    batches_processed: int
    batches_resumed: int
    source_atoms_processed: int
    source_atoms_resumed: int
    mem0_records_returned: int
    entities_returned: int
    relationships_returned: int
    relationships_quarantined: int
    empty_batches: int
    entities_imported: int
    entity_support_links_created: int
    entity_relationship_links_created: int
    calibration_signals_created: int
    warnings: tuple[str, ...]
    truncated: bool


class Mem0BootstrapService:
    """Distill existing native atoms through Mem0 and map the results back safely."""

    def __init__(
        self,
        repository: Repository,
        processor: Mem0Processor,
        *,
        importer: Mem0EntityImportService | None = None,
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
        self.importer = importer or Mem0EntityImportService(repository)
        self.atom_batch_size = atom_batch_size
        self.max_batch_chars = max_batch_chars
        self.accept_empty = accept_empty
        self.processor_profile = str(
            getattr(processor, "profile_id", f"{MEM0_BOOTSTRAP_PROFILE}:default")
        )
        self.pipeline_profile = f"{self.processor_profile}+importer:{self.importer.profile_id}"

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
        for document_ids in self.repository.iter_document_ids_chronological(namespace=namespace):
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
                    atoms = tuple(
                        atom
                        for atom in self.repository.get_atoms(atom_ids)
                        if atom.role is AtomRole.SOURCE
                    )
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
            mem0_records_returned=counters["mem0_records_returned"],
            entities_returned=counters["entities_returned"],
            relationships_returned=counters["relationships_returned"],
            relationships_quarantined=counters["relationships_quarantined"],
            empty_batches=counters["empty_batches"],
            entities_imported=counters["entities_imported"],
            entity_support_links_created=counters["entity_support_links_created"],
            entity_relationship_links_created=counters["entity_relationship_links_created"],
            calibration_signals_created=counters["calibration_signals_created"],
            warnings=tuple(
                sorted(
                    key.removeprefix("warning:") for key in counters if key.startswith("warning:")
                )
            ),
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
        batch_id = stable_id("mem0-bootstrap-batch", namespace, document_id, *source_atom_ids)
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

        processed = self.processor.add(
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
                "processor_profile": self.processor_profile,
            },
        )
        counters["mem0_records_returned"] += len(processed.memories)
        counters["entities_returned"] += len(processed.entities)
        counters["relationships_returned"] += len(processed.relationships)
        counters["relationships_quarantined"] += processed.relationships_quarantined
        for warning in processed.warnings:
            counters[f"warning:{warning}"] += 1

        if processed.entities or processed.relationships:
            imported = self.importer.import_graph(
                namespace=namespace,
                batch_id=batch_id,
                entities=processed.entities,
                relationships=processed.relationships,
            )
            counters["entities_imported"] += imported.entities_imported
            counters["entity_support_links_created"] += imported.entity_support_links_created
            counters["entity_relationship_links_created"] += (
                imported.entity_relationship_links_created
            )
            counters["calibration_signals_created"] += imported.calibration_signals_created
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
                value=1.0 if processed.relationships else 0.0,
                confidence=1.0,
                multiplier=1.0,
                provider="mem0",
                profile_version=self.pipeline_profile,
                source_reference=batch_id,
                metadata={
                    "mem0_records_returned": len(processed.memories),
                    "entities_returned": len(processed.entities),
                    "relationships_returned": len(processed.relationships),
                    "relationships_quarantined": processed.relationships_quarantined,
                    "processor_profile": self.processor_profile,
                },
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

    def _marker_id(self, *, namespace: str, batch_id: str, atom_id: str) -> str:
        return stable_id("calibration", namespace, self.pipeline_profile, batch_id, atom_id)

    @staticmethod
    def _is_mem0_output(*, source: str, metadata: Mapping[str, Any]) -> bool:
        return source.startswith("mem0:") or metadata.get("source_system") == "mem0"

from __future__ import annotations

import json
import platform
import subprocess
import tempfile
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from data_retrieval.core.identifiers import content_hash
from data_retrieval.domain.models import (
    AtomKind,
    AtomLinkRelation,
    AtomRole,
    PayloadModality,
    TagCandidateState,
    TagLevel,
    WeightEventSource,
)
from data_retrieval.ingestion.chunker import TextChunker
from data_retrieval.mem0 import Mem0BootstrapService
from data_retrieval.retrieval.models import (
    FeedbackRequest,
    QueryPlan,
    RetrievalItem,
    ScoreBreakdown,
)
from data_retrieval.retrieval.packing import EvidencePacker
from data_retrieval.services.embedding_enrichment import EmbeddingEnrichmentService
from data_retrieval.services.ingestion import IngestService
from data_retrieval.services.learning import LearningService
from data_retrieval.services.retrieval import RetrievalService
from data_retrieval.services.tag_enrichment import TagEnrichmentService
from data_retrieval.services.tag_lifecycle import TagLifecycleService
from data_retrieval.services.weight_ledger import WeightLedgerService
from data_retrieval.storage.memory import InMemoryRepository
from data_retrieval.storage.sqlite import SQLiteRepository
from data_retrieval.tagging.canonicalization import SemanticTagCanonicalizer
from data_retrieval.tagging.proposals import TagProposal
from data_retrieval.temporal import TemporalBridge


@dataclass(frozen=True, slots=True)
class GateCheck:
    name: str
    passed: bool
    expected: Any
    actual: Any
    detail: str = ""


@dataclass(frozen=True, slots=True)
class CapabilityGateReport:
    capability: str
    owner: str
    scope: str
    passed: bool
    duration_ms: float
    metrics: dict[str, float | int]
    checks: tuple[GateCheck, ...]
    profiles: dict[str, str]
    limitations: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "owner": self.owner,
            "scope": self.scope,
            "passed": self.passed,
            "duration_ms": self.duration_ms,
            "metrics": self.metrics,
            "checks": [asdict(check) for check in self.checks],
            "profiles": self.profiles,
            "limitations": list(self.limitations),
        }


@dataclass(frozen=True, slots=True)
class IsolatedCapabilitySuiteReport:
    suite_id: str
    fixture_path: str
    fixture_hash: str
    code_revision: str
    working_tree_dirty: bool
    python_version: str
    storage_adapters: tuple[str, ...]
    random_seed: int
    feature_switches: dict[str, bool]
    token_budget: int | None
    artifact_location: str | None
    duration_ms: float
    passed: bool
    gates: tuple[CapabilityGateReport, ...]

    def as_dict(self) -> dict[str, Any]:
        return {
            "suite_id": self.suite_id,
            "fixture_path": self.fixture_path,
            "fixture_hash": self.fixture_hash,
            "code_revision": self.code_revision,
            "working_tree_dirty": self.working_tree_dirty,
            "python_version": self.python_version,
            "storage_adapters": list(self.storage_adapters),
            "random_seed": self.random_seed,
            "feature_switches": self.feature_switches,
            "token_budget": self.token_budget,
            "artifact_location": self.artifact_location,
            "duration_ms": self.duration_ms,
            "passed": self.passed,
            "gate_count": len(self.gates),
            "passed_gate_count": sum(gate.passed for gate in self.gates),
            "gates": [gate.as_dict() for gate in self.gates],
        }


class _FixtureMem0Processor:
    profile_id = "fixture-mem0-output-v1"

    def __init__(self, results: tuple[dict[str, Any], ...]) -> None:
        self.results = results
        self.call_count = 0

    def add(self, messages, *, user_id, run_id, metadata):
        self.call_count += 1
        return self.results


class _FixtureTagProposer:
    evidence_source = "fixture-tag-proposer"
    proposal_version = "fixture-tags-v1"

    def __init__(self, proposals: tuple[TagProposal, ...]) -> None:
        self.proposals = proposals

    def propose_tags(self, *, text, namespace, existing_tags):
        return self.proposals


class _FixtureEmbedder:
    provider = "fixture"
    model = "isolated-channel-v1"

    def embed_documents(self, texts: tuple[str, ...]) -> tuple[tuple[float, ...], ...]:
        return tuple(self._vector(text) for text in texts)

    def embed_query(self, text: str) -> tuple[float, ...]:
        return self._vector(text)

    @staticmethod
    def _vector(text: str) -> tuple[float, ...]:
        normalized = text.casefold()
        if any(
            value in normalized
            for value in (
                "car engine",
                "automobile powertrain",
                "automobiles",
                "vehicles",
            )
        ):
            return (1.0, 0.0)
        return (0.0, 1.0)


GateFunction = Callable[[dict[str, Any]], tuple[dict[str, float | int], tuple[GateCheck, ...]]]


class IsolatedCapabilitySuite:
    """Run fast deterministic contracts without conflating them with model-quality claims."""

    def run(
        self, fixture_path: Path, *, artifact_location: Path | None = None
    ) -> IsolatedCapabilitySuiteReport:
        fixture_text = fixture_path.read_text(encoding="utf-8")
        fixture = json.loads(fixture_text)
        if not isinstance(fixture, dict) or not str(fixture.get("fixture_id", "")).strip():
            raise ValueError("isolated capability fixture needs a fixture_id")
        started = time.perf_counter()
        definitions: tuple[tuple[str, str, str, GateFunction, tuple[str, ...]], ...] = (
            (
                "canonical_core",
                "Data Retrieval core",
                "deterministic persistence contract",
                self._canonical_core,
                (),
            ),
            (
                "mem0_boundary",
                "Mem0 adapter",
                "fixed-output processor contract",
                self._mem0_boundary,
                ("Does not measure real-model fact precision, recall, latency, or cost.",),
            ),
            (
                "temporal_projection",
                "Temporal History adapter",
                "mock-summary integrity contract",
                self._temporal_projection,
                ("Does not measure model-generated summary fidelity.",),
            ),
            (
                "tags",
                "Tags",
                "fixed-proposal catalog and attachment contract",
                self._tags,
                ("Does not measure real-model proposal quality.",),
            ),
            (
                "outcome_learning",
                "Data Retrieval / Tags",
                "single attributable feedback contract",
                self._outcome_learning,
                ("Held-out quality and long-run collateral effects require a larger fixture.",),
            ),
            (
                "retrieval_channels",
                "Data Retrieval retrieval",
                "independent lexical, semantic, and tag channel contract",
                self._retrieval_channels,
                (
                    "Generated query tags and canonicalization use fixed proposals and "
                    "deterministic embeddings; real-model quality remains unmeasured.",
                ),
            ),
            (
                "evidence_packing",
                "Data Retrieval retrieval",
                "deterministic packing-policy contract",
                self._evidence_packing,
                ("Default quota values still require dataset-level tuning.",),
            ),
        )
        gates = tuple(
            self._run_gate(
                capability=name,
                owner=owner,
                scope=scope,
                fixture=self._section(fixture, name),
                function=function,
                limitations=limitations,
            )
            for name, owner, scope, function, limitations in definitions
        )
        revision, dirty = self._git_identity(Path(__file__).resolve().parents[3])
        return IsolatedCapabilitySuiteReport(
            suite_id=str(fixture["fixture_id"]),
            fixture_path=str(fixture_path),
            fixture_hash=content_hash(fixture_text),
            code_revision=revision,
            working_tree_dirty=dirty,
            python_version=platform.python_version(),
            storage_adapters=("memory", "sqlite"),
            random_seed=int(fixture.get("random_seed", 0)),
            feature_switches={
                "real_mem0_model": False,
                "real_temporal_model": False,
                "real_tag_model": False,
                "deterministic_embeddings": True,
            },
            token_budget=None,
            artifact_location=str(artifact_location) if artifact_location else None,
            duration_ms=(time.perf_counter() - started) * 1000.0,
            passed=all(gate.passed for gate in gates),
            gates=gates,
        )

    def _run_gate(
        self,
        *,
        capability: str,
        owner: str,
        scope: str,
        fixture: dict[str, Any],
        function: GateFunction,
        limitations: tuple[str, ...],
    ) -> CapabilityGateReport:
        started = time.perf_counter()
        try:
            metrics, checks = function(fixture)
        except Exception as error:  # noqa: BLE001 - a suite reports isolated failures
            metrics = {}
            checks = (
                GateCheck(
                    name="execution",
                    passed=False,
                    expected="successful execution",
                    actual=type(error).__name__,
                    detail=str(error),
                ),
            )
        return CapabilityGateReport(
            capability=capability,
            owner=owner,
            scope=scope,
            passed=all(check.passed for check in checks),
            duration_ms=(time.perf_counter() - started) * 1000.0,
            metrics=metrics,
            checks=checks,
            profiles=self._profiles(capability),
            limitations=limitations,
        )

    def _canonical_core(
        self, fixture: dict[str, Any]
    ) -> tuple[dict[str, float | int], tuple[GateCheck, ...]]:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "canonical.sqlite3"
            with SQLiteRepository(path) as repository:
                service = IngestService(repository)
                first = service.ingest_text(
                    namespace=str(fixture["namespace"]),
                    source=str(fixture["source"]),
                    text=str(fixture["text"]),
                    explicit_tags=tuple(str(tag) for tag in fixture["tags"]),
                )
                replay = service.ingest_text(
                    namespace=str(fixture["namespace"]),
                    source=str(fixture["source"]),
                    text=str(fixture["text"]),
                    explicit_tags=tuple(str(tag) for tag in fixture["tags"]),
                )
                original_atom = repository.get_atom(first.atom_ids[0])
                counts = (repository.document_count, repository.atom_count)
            with SQLiteRepository(path) as reopened:
                persisted_atom = reopened.get_atom(first.atom_ids[0])
                persisted_counts = (reopened.document_count, reopened.atom_count)
        checks = (
            self._check("idempotent_replay", True, replay.idempotent),
            self._check("stable_atom_ids", first.atom_ids, replay.atom_ids),
            self._check("persisted_counts", counts, persisted_counts),
            self._check("source_role", AtomRole.SOURCE, persisted_atom.role),
            self._check("text_modality", PayloadModality.TEXT, persisted_atom.modality),
            self._check("complete_hydration", original_atom, persisted_atom),
        )
        return {"document_count": counts[0], "atom_count": counts[1]}, checks

    def _mem0_boundary(
        self, fixture: dict[str, Any]
    ) -> tuple[dict[str, float | int], tuple[GateCheck, ...]]:
        repository = InMemoryRepository()
        paragraphs = tuple(str(value) for value in fixture["source_paragraphs"])
        ingested = IngestService(
            repository, chunker=TextChunker(max_chars=100, overlap_chars=0)
        ).ingest_text(
            namespace=str(fixture["namespace"]),
            source=str(fixture["source"]),
            text="\n\n".join(paragraphs),
        )
        facts = tuple(dict(value) for value in fixture["facts"])
        if len(ingested.atom_ids) != len(paragraphs):
            raise ValueError("Mem0 fixture paragraphs must produce one atom each")
        results = tuple(
            {
                "id": str(fact["id"]),
                "memory": str(fact["content"]),
                "support_atom_ids": [ingested.atom_ids[int(fact["support_index"])]],
            }
            for fact in facts
        )
        processor = _FixtureMem0Processor(results)
        service = Mem0BootstrapService(repository, processor, atom_batch_size=10)
        first = service.run(namespace=str(fixture["namespace"]))
        replay = service.run(namespace=str(fixture["namespace"]))
        derived = repository.list_atoms(namespace=str(fixture["namespace"]), role=AtomRole.DERIVED)
        support_links = repository.list_atom_links(
            namespace=str(fixture["namespace"]), relation=AtomLinkRelation.SUPPORTED_BY
        )
        support_by_content = {
            repository.get_atom(link.from_atom_id).content: link.to_atom_id
            for link in support_links
        }
        expected_support = {
            str(fact["content"]): ingested.atom_ids[int(fact["support_index"])] for fact in facts
        }
        batch_ids_are_separate = all(
            set(atom.metadata.get("batch_atom_ids", ())) == set(ingested.atom_ids)
            and set(atom.metadata.get("support_atom_ids", ())) == {expected_support[atom.content]}
            for atom in derived
        )
        source_chars = sum(len(value) for value in paragraphs)
        fact_chars = sum(len(str(fact["content"])) for fact in facts)
        checks = (
            self._check("all_facts_imported", len(facts), first.memories_imported),
            self._check("no_unaligned_facts", 0, first.memories_unaligned),
            self._check("derived_roles", {AtomRole.DERIVED}, {atom.role for atom in derived}),
            self._check("exact_fact_support", expected_support, support_by_content),
            self._check("batch_membership_separate", True, batch_ids_are_separate),
            self._check("replay_resumed", 1, replay.batches_resumed),
            self._check("processor_called_once", 1, processor.call_count),
        )
        return {
            "source_atom_count": len(ingested.atom_ids),
            "fact_count": len(derived),
            "support_link_count": len(support_links),
            "compression_ratio": fact_chars / source_chars,
        }, checks

    def _temporal_projection(
        self, fixture: dict[str, Any]
    ) -> tuple[dict[str, float | int], tuple[GateCheck, ...]]:
        repository = InMemoryRepository()
        source_ids: list[str] = []
        for event in fixture["events"]:
            result = IngestService(repository).ingest_text(
                namespace=str(fixture["namespace"]),
                source=str(event["source"]),
                text=str(event["text"]),
                occurred_at=datetime.fromisoformat(str(event["occurred_at"])),
                metadata={"timeline_id": str(fixture["timeline_id"])},
            )
            source_ids.extend(result.atom_ids)
        with tempfile.TemporaryDirectory() as directory:
            projection = TemporalBridge().project(
                namespace=str(fixture["namespace"]),
                timeline_id=str(fixture["timeline_id"]),
                atoms=repository.get_atoms(tuple(source_ids)),
                timezone_name="UTC",
                range_start=datetime.fromisoformat(str(fixture["range_start"])),
                range_end=datetime.fromisoformat(str(fixture["range_end"])),
                state_path=Path(directory) / "temporal.sqlite3",
            )
        repository.persist_ingestion(projection.bundle)
        granularities = {str(atom.metadata["granularity"]) for atom in projection.bundle.atoms}
        six_hour = next(
            atom for atom in projection.bundle.atoms if atom.metadata["granularity"] == "six_hour"
        )
        lineage = {link.to_atom_id for link in repository.get_atom_links(six_hour.atom_id)}
        expected_granularities = set(str(value) for value in fixture["expected_granularities"])
        checks = (
            self._check("calendar_hierarchy", expected_granularities, granularities),
            self._check(
                "derived_roles",
                {AtomRole.DERIVED},
                {atom.role for atom in projection.bundle.atoms},
            ),
            self._check("six_hour_source_lineage", set(source_ids), lineage),
            self._check(
                "versioned_metadata",
                True,
                all("model_config_hash" in atom.metadata for atom in projection.bundle.atoms),
            ),
        )
        return {
            "source_atom_count": len(source_ids),
            "summary_atom_count": len(projection.bundle.atoms),
            "lineage_link_count": len(projection.bundle.atom_links),
        }, checks

    def _tags(
        self, fixture: dict[str, Any]
    ) -> tuple[dict[str, float | int], tuple[GateCheck, ...]]:
        repository = InMemoryRepository()
        ingested = IngestService(repository).ingest_text(
            namespace=str(fixture["namespace"]),
            source=str(fixture["source"]),
            text=str(fixture["text"]),
        )
        proposals = tuple(
            TagProposal(
                text=str(value["text"]),
                confidence=float(value["confidence"]),
                level=TagLevel(str(value["level"])),
            )
            for value in fixture["proposals"]
        )
        service = TagEnrichmentService(repository, _FixtureTagProposer(proposals))
        first = service.enrich_document(ingested.document_id)
        replay = service.enrich_document(ingested.document_id)
        quarantined = repository.list_tag_candidates(
            namespace=str(fixture["namespace"]), state=TagCandidateState.PROPOSED
        )
        initial_tag_count = len(repository.list_tags(str(fixture["namespace"])))
        initial_edge_count = len(repository.list_atom_tags(str(fixture["namespace"])))
        lifecycle = TagLifecycleService(repository)
        for candidate in quarantined:
            lifecycle.promote(candidate.candidate_id)
        actual = {tag.canonical_text for tag in repository.list_tags(str(fixture["namespace"]))}
        expected = {str(value) for value in fixture["expected_canonical_tags"]}
        intersection = actual & expected
        precision = len(intersection) / len(actual) if actual else 0.0
        recall = len(intersection) / len(expected) if expected else 0.0
        checks = (
            self._check(
                "novel_proposals_quarantined",
                (0, 0),
                (initial_tag_count, initial_edge_count),
            ),
            self._check(
                "structured_levels_preserved",
                {"broad", "specific"},
                {value.level.value for value in quarantined},
            ),
            self._check("canonical_tag_set", expected, actual),
            self._check("duplicate_proposal_collapsed", len(expected), len(first.candidate_ids)),
            self._check(
                "atom_tag_attachments",
                len(expected),
                len(repository.list_atom_tags(str(fixture["namespace"]))),
            ),
            self._check("replay_idempotent", True, replay.idempotent),
        )
        return {
            "proposal_count": len(proposals),
            "canonical_tag_count": len(actual),
            "precision": precision,
            "recall": recall,
        }, checks

    def _outcome_learning(
        self, fixture: dict[str, Any]
    ) -> tuple[dict[str, float | int], tuple[GateCheck, ...]]:
        repository = InMemoryRepository()
        atom_by_source: dict[str, str] = {}
        for document in fixture["documents"]:
            result = IngestService(repository).ingest_text(
                namespace=str(fixture["namespace"]),
                source=str(document["source"]),
                text=str(document["text"]),
                explicit_tags=tuple(str(value) for value in document["tags"]),
            )
            atom_by_source[str(document["source"])] = result.atom_ids[0]
        selected_id = atom_by_source["selected"]
        collateral_id = atom_by_source["collateral"]
        namespace = str(fixture["namespace"])
        selected_before = self._tag_weight(repository, namespace, selected_id, "architecture")
        collateral_before = self._tag_weight(repository, namespace, collateral_id, "architecture")
        retrieval = RetrievalService(repository).retrieve(
            QueryPlan(
                query=str(fixture["query"]),
                namespace=str(fixture["namespace"]),
                query_tags=tuple(str(value) for value in fixture["query_tags"]),
            )
        )
        returned = {item.atom_id for item in retrieval.items}
        if selected_id not in returned:
            raise ValueError("learning fixture selected atom was not retrieved")
        learned = LearningService(repository).apply_feedback(
            FeedbackRequest(
                feedback_id="isolated-learning-feedback",
                retrieval_id=retrieval.retrieval_id,
                selected_atom_ids=(selected_id,),
                outcome="positive",
            )
        )
        selected_after = self._tag_weight(repository, namespace, selected_id, "architecture")
        collateral_after = self._tag_weight(repository, namespace, collateral_id, "architecture")
        replay_rejected = False
        try:
            LearningService(repository).apply_feedback(
                FeedbackRequest(
                    feedback_id="isolated-learning-feedback",
                    retrieval_id=retrieval.retrieval_id,
                    selected_atom_ids=(selected_id,),
                    outcome="positive",
                )
            )
        except ValueError:
            replay_rejected = True
        weight_audit = WeightLedgerService(repository).audit_namespace(namespace)
        feedback_weight_events = tuple(
            event
            for event in repository.list_weight_events(namespace=namespace)
            if event.source_type is WeightEventSource.FEEDBACK
        )
        selected_delta = selected_after - selected_before
        collateral_delta = collateral_after - collateral_before
        checks = (
            self._check(
                "selected_weight_delta",
                float(fixture["expected_selected_delta"]),
                round(selected_delta, 10),
            ),
            self._check(
                "collateral_weight_delta",
                float(fixture["expected_collateral_delta"]),
                round(collateral_delta, 10),
            ),
            self._check("credited_only_selected", (selected_id,), learned.credited_atom_ids),
            self._check("feedback_replay_rejected", True, replay_rejected),
            self._check("weight_ledger_reconstructs_aggregates", True, weight_audit.passed),
            self._check("feedback_weight_events", True, bool(feedback_weight_events)),
        )
        return {
            "selected_delta": selected_delta,
            "collateral_delta": collateral_delta,
            "atom_tag_updates": learned.atom_tag_updates,
            "weight_event_count": weight_audit.event_count,
        }, checks

    def _retrieval_channels(
        self, fixture: dict[str, Any]
    ) -> tuple[dict[str, float | int], tuple[GateCheck, ...]]:
        repository = InMemoryRepository()
        target = dict(fixture["target"])
        distractor = dict(fixture["distractor"])
        target_result = IngestService(repository).ingest_text(
            namespace=str(fixture["namespace"]),
            source=str(target["source"]),
            text=str(target["text"]),
            explicit_tags=tuple(str(value) for value in target["tags"]),
        )
        IngestService(repository).ingest_text(
            namespace=str(fixture["namespace"]),
            source=str(distractor["source"]),
            text=str(distractor["text"]),
            explicit_tags=tuple(str(value) for value in distractor["tags"]),
        )
        target_id = target_result.atom_ids[0]
        embedder = _FixtureEmbedder()
        EmbeddingEnrichmentService(repository, embedder).enrich_namespace(str(fixture["namespace"]))
        lexical = repository.search_lexical_hits(
            namespace=str(fixture["namespace"]),
            query=str(fixture["lexical_query"]),
            limit=2,
        )
        semantic = repository.search_semantic_hits(
            namespace=str(fixture["namespace"]),
            provider=embedder.provider,
            model=embedder.model,
            query_vector=embedder.embed_query(str(fixture["semantic_query"])),
            limit=2,
        )
        tag = repository.search_tag_hits(
            namespace=str(fixture["namespace"]),
            canonical_tags=tuple(str(value) for value in fixture["tag_query"]),
            limit=2,
        )
        query_tag_proposer = _FixtureTagProposer(
            (
                TagProposal(
                    text=str(fixture["generated_query_tag"]),
                    confidence=1.0,
                ),
            )
        )
        tag_first = RetrievalService(
            repository,
            tag_proposer=query_tag_proposer,
            tag_canonicalizer=SemanticTagCanonicalizer(embedder),
        ).retrieve(
            QueryPlan(
                query=str(fixture["semantic_query"]),
                namespace=str(fixture["namespace"]),
            )
        )
        checks = (
            self._check("lexical_top_hit", target_id, lexical[0].atom_id),
            self._check("semantic_top_hit", target_id, semantic[0].atom_id),
            self._check("tag_top_hit", target_id, tag[0].atom_id),
            self._check("generated_tag_top_hit", target_id, tag_first.items[0].atom_id),
            self._check(
                "generated_tag_canonicalized",
                (str(fixture["canonical_query_tag"]),),
                tag_first.diagnostics["query_tags"],
            ),
        )
        return {
            "lexical_hit_count": len(lexical),
            "semantic_hit_count": len(semantic),
            "tag_hit_count": len(tag),
            "generated_tag_result_count": len(tag_first.items),
        }, checks

    def _evidence_packing(
        self, fixture: dict[str, Any]
    ) -> tuple[dict[str, float | int], tuple[GateCheck, ...]]:
        source_count = int(fixture["source_count"])
        derived_count = int(fixture["derived_count"])
        ranked = [
            self._packing_item(
                f"derived-{index}",
                AtomRole.DERIVED,
                1.0 - index * 0.01,
                f"derived-item{index}",
            )
            for index in range(derived_count)
        ]
        ranked.extend(
            self._packing_item(
                f"source-{index}", AtomRole.SOURCE, 0.5 - index * 0.01, f"source-{index}"
            )
            for index in range(source_count)
        )
        packed = EvidencePacker().pack(ranked, top_k=int(fixture["top_k"]))
        diagnostics = packed.diagnostics
        checks = (
            self._check(
                "source_quota",
                int(fixture["expected_source_selected"]),
                diagnostics["source_selected"],
            ),
            self._check(
                "derived_cap",
                int(fixture["expected_derived_limit"]),
                diagnostics["derived_limit"],
            ),
            self._check(
                "bounded_total",
                int(fixture["expected_total_selected"]),
                len(packed.items),
            ),
            self._check("underfill_is_explicit", True, diagnostics["underfilled"]),
        )
        return {
            "ranked_count": len(ranked),
            "selected_count": len(packed.items),
            "source_selected": int(diagnostics["source_selected"]),
            "derived_limit": int(diagnostics["derived_limit"]),
        }, checks

    @staticmethod
    def _packing_item(atom_id: str, role: AtomRole, score: float, content: str) -> RetrievalItem:
        return RetrievalItem(
            atom_id=atom_id,
            content=content,
            kind=AtomKind.SOURCE,
            occurred_at=None,
            role=role.value,
            score=ScoreBreakdown(final=score),
            metadata={},
            atom_role=role,
        )

    @staticmethod
    def _tag_weight(
        repository: InMemoryRepository,
        namespace: str,
        atom_id: str,
        canonical: str,
    ) -> float:
        tags = {tag.tag_id: tag for tag in repository.list_tags(namespace)}
        return next(
            edge.weight_raw
            for edge in repository.atom_tags_for(atom_id)
            if tags[edge.tag_id].canonical_text == canonical
        )

    @staticmethod
    def _check(name: str, expected: Any, actual: Any, detail: str = "") -> GateCheck:
        return GateCheck(
            name=name,
            passed=expected == actual,
            expected=IsolatedCapabilitySuite._json_value(expected),
            actual=IsolatedCapabilitySuite._json_value(actual),
            detail=detail,
        )

    @staticmethod
    def _json_value(value: Any) -> Any:
        if isinstance(value, datetime):
            return value.isoformat()
        if isinstance(value, set | frozenset):
            return sorted(IsolatedCapabilitySuite._json_value(item) for item in value)
        if isinstance(value, tuple | list):
            return [IsolatedCapabilitySuite._json_value(item) for item in value]
        if isinstance(value, dict):
            return {
                str(key): IsolatedCapabilitySuite._json_value(item)
                for key, item in value.items()
            }
        if hasattr(value, "value"):
            return IsolatedCapabilitySuite._json_value(value.value)
        if hasattr(value, "__dataclass_fields__"):
            return IsolatedCapabilitySuite._json_value(asdict(value))
        return value

    @staticmethod
    def _section(fixture: dict[str, Any], name: str) -> dict[str, Any]:
        section = fixture.get(name)
        if not isinstance(section, dict):
            raise ValueError(f"isolated capability fixture is missing {name}")
        return section

    @staticmethod
    def _profiles(capability: str) -> dict[str, str]:
        profiles = {
            "canonical_core": {"schema": "role-modality-v1"},
            "mem0_boundary": {
                "processor": _FixtureMem0Processor.profile_id,
                "support": "processor_declared",
            },
            "temporal_projection": {"summarizer": "temporal-history-mock"},
            "tags": {"proposer": _FixtureTagProposer.proposal_version},
            "outcome_learning": {"policy": "bounded-feedback-v1"},
            "retrieval_channels": {
                "embedding": _FixtureEmbedder.model,
                "query_tags": _FixtureTagProposer.proposal_version,
            },
            "evidence_packing": {"policy": "default-packing-v1"},
        }
        return profiles[capability]

    @staticmethod
    def _git_identity(start: Path) -> tuple[str, bool]:
        revision = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
        dirty = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=start,
            capture_output=True,
            text=True,
            check=False,
        )
        return revision.stdout.strip() or "unknown", bool(dirty.stdout.strip())

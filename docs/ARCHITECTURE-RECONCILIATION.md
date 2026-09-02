# Architecture reconciliation

Date: 2026-09-02

This document records the project scope recovered from the original Tags implementation,
DebUI/Cortex specifications, the architecture handoff, and the current Data Retrieval runtime.
It exists to prevent an intentionally deferred capability from being mistaken for a rejected
idea during later simplification.

## One-sentence intent

Data Retrieval is an auditable associative memory and capability substrate: canonical atoms
preserve evidence, semantic tags decide what becomes relevant, temporal projections explain
when and how state changed, outcome-supported relationships capture what proves useful
together, stable patterns may become higher-order groups, and validated successful episodes
may become retrievable procedures for smaller models.

## Recovered mental model

```text
query
  -> generated query concepts
  -> canonical tag graph + lexical and semantic evidence
  -> relevant source atoms, derived memories, temporal regions, groups, or skills
  -> role-aware evidence packing or an application-specific handler
  -> exact evidence or an executable capability
```

Semantic structure is the primary address space. Temporal structure reconstructs chronology
inside a relevant semantic region. Learned association records repeated usefulness. Procedural
order remains a separate validated structure and is never inferred from co-use alone.

## Implemented now

- Canonical documents, atoms, tags, atom-tag weights, tag relations, and atom links.
- Separate evidence role and payload modality, with legacy `AtomKind` compatibility.
- Raw-before-derived ingestion and a no-delete source-evidence invariant.
- Catalog-first exact and optional semantic tag canonicalization.
- Tag, lexical, semantic, one-hop relationship, adjacency, and conditional temporal retrieval.
- Temporal History projections as derived atoms with lineage.
- Mem0 as a replaceable extraction/calibration provider with fact-level source support.
- Attributable feedback, bounded learning, retrieval audit, and interaction atoms.
- Role/provenance-aware evidence packing and isolated deterministic capability gates.
- Optional query-tag generation inside `RetrievalService` and the LongMemEval pipeline.

## Recovered gaps that require repair

### 1. The original tag-first entry path is not consistently active

The original Tags system generated concepts from a natural-language query, mapped them onto
existing semantic handles, and used the resulting tag-to-atom index. `RetrievalService`
supports this, but the normal CLI and general evaluator did not consistently supply a tag
proposer. Tests using explicit query tags did not prove the original behavior.

Required repair:

- make the query tag proposer an optional normal retrieval dependency;
- preserve lexical and semantic fallback when it is unavailable;
- gate natural-language query -> generated concept -> catalog match -> atom retrieval;
- record generated/canonical query tags and provider degradation in diagnostics.

Repair status: ADR-0010, normal CLI and general evaluator wiring, generated-tag canonicalization
coverage, and provider-failure fallback coverage are implemented. Real-model quality
measurement remains pending.

### 2. The tag proposal and lifecycle contracts are too thin

`TagProposal` currently carries only text and confidence. Broad/specific level is inferred from
whether a normalized tag contains whitespace. A `proposed_new` tag has no promotion, rejection,
merge, or alias-history lifecycle, yet proposed tags can enter serving paths.

Required design before mutation:

- structured proposal fields for semantic level, aliases, parent candidates, evidence, and
  generation trace;
- explicit serving rules for proposed tags;
- evidence-gated promotion, rejection, alias merge, and conflict handling;
- separate quality metrics for proposal precision, coverage, level accuracy, hierarchy
  accuracy, catalog-match correctness, and degraded behavior.

Recommended serving policy: proposed tags may support a bounded exact direct match, clearly
labeled as provisional, but must not act as canonical catalog hints or recursive relationship
expansion nodes until promoted. Thresholds must be selected by evaluation rather than copied
from historical code.

### 3. Learned state is not fully reconstructable

Calibration signals are immutable and replay-safe, but ordinary feedback still updates serving
aggregates directly. Higher-order group evolution and skill validation need a complete history
of why atom-tag, tag-tag, atom-atom, and future group weights changed.

Required repair:

- one immutable, idempotent weight-event contract;
- event-backed aggregates for calibration, Mem0, feedback, `CO_USED`, atom-tag, and tag-relation
  updates;
- conflict observations, counterexamples, replay, aggregate rebuild, and audit tests;
- versioned learning policies and rollback-compatible serving snapshots.

Automatic group promotion must not precede this substrate.

### 4. Payload independence is only partially represented

Role and modality are now independent, but an atom still requires inline text content. The old
Cortex model separated graph identity from `payload_ref` and `payload_inline_small`.

Required design:

- immutable payload identity/hash;
- optional stable payload locator plus cached inline-small or text projection;
- handler/add-on key independent of evidence role and payload modality;
- source/version rules for moved or changed external objects;
- replaceable derived representations keyed by provider/model/profile.

Text-first ingestion remains valid while this contract is designed.

### 5. The associative graph is currently shallow

The runtime performs bounded one-hop tag, `CO_USED`, and adjacency expansion. Historical Cortex
also proposed layered tag traversal, marginal-gain/rate-of-change stopping, and a broader
semantic fallback when normal coverage was weak.

These are mechanisms to evaluate, not formulas to copy. Any restoration requires visited sets,
per-layer and total budgets, relation-aware paths, confidence-calibrated triggers, and an
ablation against fixed-depth retrieval.

### 6. Higher-order groups remain unresolved

Historical `GroupTag` represented a weighted emergent cluster of tags with cohesion,
promotion/demotion windows, outcome quality, counterexamples, and hysteresis. The later handoff
generalized the idea to heterogeneous atoms and recursive capability groups.

Before implementation, compare:

1. a separate Group entity;
2. group-as-atom with weighted membership links;
3. tag-only `GroupTag`.

The evaluation must measure retrieval or context-cost improvement, stability, exact-evidence
expansion, false-group rate, and downstream task lift. Frequent co-occurrence alone is not a
promotion signal.

### 7. Semantic-temporal traversal remains an experiment

The current temporal lens correctly applies time after relevance discovery. A future additive
experiment may score coarse Temporal History nodes by semantic/tag relevance and descend with
a bounded multi-branch beam. It must preserve current/as-of eligibility, exact-source access,
branch budgets, and summary-versus-source metrics.

### 8. Irrelevant context needs an abstention boundary

Historical experiments demonstrated context poisoning: when irrelevant memory was forced into
a prompt, a model invented a connection. `low_confidence` is diagnostic, but a downstream
consumer still needs an explicit policy to omit irrelevant evidence rather than force a story.

Required repair:

- calibrate relevance/coverage confidence;
- make empty context a valid result;
- expose abstention reasons;
- test irrelevant-query and provider-degradation cases;
- keep this separate from temporal role and source/derived trust.

## Preserved future capabilities

- evidence-backed broad/specific tag hierarchies;
- bounded layered tag expansion and semantic fail-safe;
- immutable weight history, conflicts, negative direct-plus-one-hop learning, and rollback;
- global/project/user scope profiles and privacy-aware personalization;
- hot/warm/cold exploration and adaptive channel profiles with control groups;
- source adapters for chats, code ASTs, Git history, structured events, and media;
- emergent weighted groups and group-of-group capability hierarchies;
- structured process/skill atoms with preconditions, steps, branches, checks, tools,
  counterexamples, environment/version validity, and source lineage;
- a skill-consumer add-on that gives small models concrete state-aware procedures;
- an autonomous Mac worker only after an always-reachable protected canonical database exists.

## Ordered repair plan

1. **Implemented (contract level):** restore and gate the optional tag-first query path in
   normal retrieval and general evaluation. Real-model quality remains part of step 5.
2. Specify and test the structured tag proposal and proposed-tag lifecycle.
3. Implement the unified immutable weight-event ledger and aggregate rebuild.
4. Record the payload-reference/handler contract without disrupting text ingestion.
5. Run real-model tag, Mem0, and Temporal quality gates.
6. Run pairwise integrations with matched inputs and budgets.
7. Restore bounded recursive tag traversal and confidence-triggered fallback experimentally.
8. Decide the group representation only after weight history and group quality gates exist.
9. Design procedural memory separately and test small-model uplift against ordinary RAG and
   the current evidence pack.

## Anti-goals

- Do not reduce the project to pgvector plus metadata.
- Do not make time the primary address space for ordinary recall.
- Do not treat proposed tags as proven canonical concepts.
- Do not interpret co-use as causality, factual support, or procedural order.
- Do not let summaries, Mem0 memories, groups, or skills replace their source evidence.
- Do not reinforce an item merely because it was returned.
- Do not copy historical thresholds or gravity formulas without an ablation.
- Do not require Neo4j solely because the logical model is a graph.
- Do not hard-wire one model, embedding profile, storage engine, or orchestration framework into
  domain semantics.

## Source authorities

- `Data_Retrieval_Architecture_Agent_Handoff.docx`
- `docs/CONTEXT_LEDGER.md`
- `docs/CAPABILITY-PRESERVATION-PLAN.md`
- Original Tags `chunker.py`, `retrieve.py`, `db_operations.py`, and design extracts
- DebUI `subprojects/data-memory/docs/VISION.md`
- DebUI `subprojects/data-memory/docs/LEGACY_TO_CORTEX_MAPPING.md`
- Cortex tag generation, tag relation/group, retrieval math, learning math, graph schema, and
  evaluation specifications
- DebUI `Perfecting Graph Memory Retrieval.md`, treated as conceptual history rather than
  current normative law

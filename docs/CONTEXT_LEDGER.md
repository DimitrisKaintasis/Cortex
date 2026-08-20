# Context and architecture ledger

This ledger prevents accepted project behavior from disappearing when code is migrated or
an implementation is deferred. It reconciles the Tags-Project, DevUI, DebUI/Cortex, and
Temporal History revisions. A deferred item remains visible with its dependencies and is
not equivalent to a rejected item.

## Invariants

- Atoms remain the canonical, payload-agnostic unit of stored information.
- PostgreSQL owns durable atoms, tags, relationships, embeddings, events, and calibration
  provenance. External models and Mem0 are replaceable providers, not sources of truth.
- Source content is never deleted or rewritten by learning.
- Factual links (`SUMMARIZES`, `DERIVED_FROM`, `SUPERSEDES`, `ADJACENT_TO`, conflicts) stay
  separate from behavioral links (`CO_USED`) and weighted tag associations.
- Returning an item does not reinforce it. Learning requires attributable outcome evidence.
- Every initial or imported weight signal is versioned, idempotent, and replayable.
- Temporal summaries are derived atoms and retain lineage to source atoms.
- Ordered procedures/steps remain an additive structure; atom relationships do not encode
  procedural order.

## Required foundation — implemented

| Capability | Current implementation | Legacy reason |
|---|---|---|
| Teacher/student cold start | Entity density, code structure, and corpus-relative rarity produce versioned calibration signals and relative weight lifts | DevUI teacher ingestion and pipeline |
| Mem0 bootstrap/training feed | JSON/JSONL import plus a chronological reverse bridge for existing atoms; bounded batches, native distilled memories, exact and optional 0.92 semantic dedupe, source lineage, retryable empty output, conflict links, and 2x calibration/learning | DebUI plans, Mem0 service, and learning math spec |
| Initial graph relationships | Atom-tag priors, tag co-occurrence, lexical broad/specific hierarchy, and source adjacency | Original Tags ingestion and retrieval documents plus Cortex tag specs |
| Outcome learning | Retrieval audit, selected-evidence attribution, positive/negative updates, CO_USED links, and tag relationship updates | Original used-chunk loop and Cortex interaction service |
| First-class interactions | User and assistant interaction atoms, transcript references, evidence lineage, optional attributable feedback | Cortex data model and interaction service |
| Catalog-first tags | Exact normalization followed by optional semantic catalog matching at 0.90 before proposing a new tag | Accepted tag canonicalization spec |
| Temporal retrieval | Temporal History summary atoms, current/as-of/range/history modes, supersession, and relative-time parsing against the question date | Temporal integration and LongMemEval requirements |
| Large-data replay | PostgreSQL/pgvector, staged ingestion, batched calibration backfill, and embedding reuse | Scale migration and benchmark requirements |

## Accepted next layers

These ideas remain accepted, but should be added only with evaluation evidence because they
change ranking behavior rather than restore a missing prerequisite.

- Explicit GroupTags represented as native tags plus weighted `CONTAINS` relations; automatic
  promotion or skill distillation requires a separate quality gate.
- Negative direct-plus-one-hop forgetting, with a conflict buffer before uncertain evidence is
  penalized repeatedly.
- Maturity-based exploration and hot/warm/cold result sampling.
- Evaluation-driven channel-weight profiles with snapshots and rollback.
- Global-plus-project namespace blending and user-scoped personalization.
- Result diversification, raw-evidence quotas, and broader semantic fallback thresholds.
- Source-specific adapters for code ASTs, Git history, chats, and other structured inputs.

## Intentionally separate future structure

- Refining frequently useful atoms into concise instructions.
- Extracting ordered steps, prerequisites, branches, and outcomes.
- Training small models on those ordered structures.

These may reference atoms but must not replace atom-to-atom associative weights.

## Superseded or rejected

- Neo4j as a required graph database: PostgreSQL adjacency tables and recursive queries keep one
  canonical transactional store.
- MongoDB/Pinecone as additional canonical stores: they add synchronization failure modes.
- Mem0 as a live retrieval dependency: imported native evidence must remain usable while Mem0 is
  offline or upgraded.
- LangChain/LangGraph as architectural requirements: orchestration libraries are optional edges.
- Automatic reinforcement of every returned result: this creates a self-confirming ranking loop.
- Automatic deletion/pruning of source atoms: refinement is additive and the no-delete invariant
  wins over the old Gardener pruning proposal.
- Copying historical weight formulas without provenance or versioning.

## Legacy source map

- Tags-Project: `Adding New Data to the Database`, `Information Retrieval Process`,
  `Weight Updating Mechanisms`, `MongoDB Schema`, and the Maitsus branch retrieval revisions.
- DevUI: `Perfecting Graph Memory Retrieval.md`, teacher ingestion, gravity, retrieval manager,
  and Gardener experiments.
- DebUI/Cortex: root and data-memory `PLAN.md`/`PLAN2.md`, tag/group and learning math specs,
  `mem0_service.py`, `interaction_service.py`, and working-model review.
- Temporal History: package, dataset, and paper integration represented by ADR-0002.

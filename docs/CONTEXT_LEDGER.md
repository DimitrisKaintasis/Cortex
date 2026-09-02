# Context and architecture ledger

This ledger prevents accepted project behavior from disappearing when code is migrated or
an implementation is deferred. It reconciles the Tags-Project, DevUI, DebUI/Cortex, and
Temporal History revisions. A deferred item remains visible with its dependencies and is
not equivalent to a rejected item.

## Invariants

- Atoms remain the canonical, payload-agnostic unit of stored information.
- PostgreSQL owns durable atoms, tags, relationships, embeddings, events, and calibration
  provenance. External models and Mem0 are replaceable providers, not sources of truth.
- Source content is never deleted or rewritten by learning. Owner-authorized retention,
  tombstoning, export, and privacy/legal erasure are separate audited operations.
- Factual links (`SUMMARIZES`, `DERIVED_FROM`, `SUPERSEDES`, `ADJACENT_TO`, conflicts) stay
  separate from behavioral links (`CO_USED`) and weighted tag associations.
- Returning an item does not reinforce it. Learning requires attributable outcome evidence.
- Every initial or imported weight signal is versioned, idempotent, and replayable.
- Temporal summaries are derived atoms and retain lineage to source atoms.
- Versioned processor outputs have active, shadow, superseded, or rejected serving state; old
  and new projections do not all compete merely because they are retained for audit.
- Ordered procedures/steps remain an additive structure; atom relationships do not encode
  procedural order.
- The future global graph shares concept identity and promoted capability, not implicit access
  to private evidence.
- Individual outcomes create scoped observations; global serving changes require aggregation,
  privacy/abuse gates, and a versioned snapshot.
- Raw behavioral support is non-negative and unbounded; bounded relative influence is calculated
  only inside an explicit relation/scope/query neighborhood.
- Learned behavioral attention may decay or become dormant, but provenance and event history do
  not.

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
- Scope-aware blending profiles built on the accepted collective graph contract rather than a
  fixed global/project ratio.
- Result diversification, raw-evidence quotas, and broader semantic fallback thresholds.
- Source-specific adapters for code ASTs, Git history, chats, and other structured inputs.

## Recovered repair dependencies — accepted 2026-09-02

The architecture audit in `ARCHITECTURE-RECONCILIATION.md` identified capabilities that were
documented historically but not completely protected by current runtime tests.

- The optional generated query-tag path is part of normal retrieval, not only benchmark
  plumbing. It must degrade to lexical/semantic retrieval when inference is unavailable.
- `TagProposal` must evolve beyond text/confidence to preserve semantic level, hierarchy
  candidates, aliases, evidence, and generation trace.
- `proposed_new` needs an explicit serving and promotion lifecycle. Proposal state must not be
  merely decorative.
- All learned aggregate weights must become reconstructable from one immutable event history
  before automatic groups or procedures are promoted.
- Payload modality is not the complete universal-atom contract; payload reference, handler,
  immutable identity, and derived projection semantics remain to be decided.
- A downstream consumer must be allowed to abstain from injecting irrelevant memory even when
  retrieval produced low-confidence candidates.
- Semantic-temporal beam traversal, emergent groups, and procedural memory remain accepted
  experiments/layers, but depend on the repaired tag and learning substrate.

## Intentionally separate future structure

- Refining frequently useful atoms into concise instructions.
- Extracting ordered steps, prerequisites, branches, and outcomes.
- Training small models on those ordered structures.

These may reference atoms but must not replace atom-to-atom associative weights.

## Tag lifecycle repair — implemented 2026-09-02

- ADR-0011 separates untrusted atom-specific `TagCandidate` records from canonical serving
  tags.
- Model proposals now include explicit broad/specific level; persisted candidates include
  confidence, producer, proposal version, resolution, and timestamps.
- Novel candidates create no serving tag or atom-tag edge. Exact/semantic catalog matches may
  resolve immediately; review supports atomic promote, merge, and reject operations.
- Explicit user tags are canonical and have distinct `explicit` provenance.
- Legacy migrations preserve old explicit tags while quarantining model-only `proposed_new`
  relationships.
- This lifecycle repair unblocked the immutable weight-event history completed below.

## Immutable weight ledger — implemented 2026-09-02

- ADR-0012 makes append-only `weight_events` authoritative for atom-tag, atom-link, and
  tag-relation weights while retaining fast serving aggregates.
- Ingestion, tag review, calibration/Mem0, and explicit feedback record versioned before/after
  transitions atomically with aggregate changes.
- Existing databases receive labeled migration baselines; unavailable pre-ledger detail is not
  fabricated.
- `audit-weights` reconstructs every edge and reports missing history, broken chains, and cache
  divergence. `--repair-aggregates` explicitly restores only the serving cache.
- Automatic groups now have the required history substrate, but still require their own quality
  gate.

## Collective capability graph — accepted contract 2026-09-02

- ADR-0013 makes the long-term product a privacy-preserving, model-agnostic collective
  capability substrate.
- Globally stable concepts and promoted routing/procedure capability compose with organization,
  project, user, and private evidence overlays. Namespace alone is not the scope or security
  contract.
- Private payloads and atom attachments remain scoped by default. Only minimized, consented,
  sensitivity-checked, bounded observations may enter independent-contributor aggregation.
- Individual outcomes never mutate global serving state directly. Shadow evaluation,
  privacy/abuse gates, canary promotion, snapshots, and rollback stand between observations and
  shared behavior.
- The decisive test is cross-user improvement on disjoint private atoms through shared concepts,
  accompanied by content-leakage and poisoning probes.

## Unbounded support and forgetting — accepted contract 2026-09-02

- ADR-0014 restores the later Cortex decision that `HAS_TAG`, `RELATED_TO`, and `CONTAINS`
  support is non-negative and unbounded.
- Positive and negative support remain distinct; confidence, reliability, cohesion, and final
  serving influence remain bounded.
- Retrieval uses relative `log1p` normalization within explicit local comparison sets. Raw
  weights from unrelated scopes, relation types, or paths are never directly multiplied.
- Passive behavioral decay is proportional and lazy; verified bad outcomes add bounded negative
  observations immediately. Provenance/factual links never decay from non-use.
- Hot/warm/cold/dormant/inhibited state controls serving indexes without deleting history, and
  renewed evidence may reactivate a relationship.
- The first bounded `0.5`, `+0.05`, `-0.02 every N ticks` Tags formula and the later unbounded
  Cortex formula are preserved as experiment baselines, not copied as untested constants.
- The active operational sequence is maintained in `EXECUTION-ROADMAP.md`.
- The immediate work is an isolated, deterministic collective-transfer experiment after the
  current baseline is recorded. It may define a minimal experimental export boundary but must
  not change canonical schemas or normal serving behavior.
- The payload reference/handler contract, including scope, visibility, and contribution policy,
  becomes the next canonical repair only if the collective experiment reaches a proceed
  decision.

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

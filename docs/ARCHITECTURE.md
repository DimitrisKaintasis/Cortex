# Data Retrieval architecture

- Status: Authoritative target architecture
- Date: 2026-09-02
- Supersedes: component-boundary and implementation-order guidance scattered across the
  migration and capability-preservation documents

## Product boundary

Data Retrieval is an auditable, adaptive, model-independent capability substrate. It preserves
canonical source evidence, accepts replaceable derived interpretations, organizes all
representations through weighted concepts, and produces a compact evidence pack or validated
procedure for an AI consumer. Its long-term scope includes privacy-preserving collective
learning: private graphs may contribute bounded outcome observations to a shared concept graph
without making their source atoms globally accessible.

It is not a second Mem0 implementation and it is not a reimplementation of Temporal History.
Mem0 and Temporal History are processors. They may interpret canonical atoms, but they do not
own canonical truth or final retrieval policy.

## Architectural rules

1. Raw evidence is persisted before optional inference.
2. A processor never overwrites or silently reclassifies raw evidence.
3. Every derived artifact identifies its provider, version, and supporting canonical atoms.
4. Processors emit artifacts and proposals; the core accepts, versions, or rejects them.
5. Factual/provenance relationships and learned/behavioral relationships remain separate.
6. The core owns final query planning, eligibility, fusion, and evidence packing.
7. Each capability must pass an isolated test before combined results may justify promotion or
   replacement. Early integration smoke tests remain allowed and encouraged.
8. Upstream components remain replaceable behind adapters.
9. Globally shared concepts and capability remain separate from private evidence overlays.
10. Individual outcomes create scoped observations; they never mutate global serving state
    directly.
11. Raw learned support may accumulate without saturation, but retrieval consumes bounded
    relative influence within explicit comparison neighborhoods.
12. Evidence and observations are durable; learned behavioral attention may decay, become
    dormant, or be inhibited without deleting history.
13. Source evidence is immutable to processors and learning, not exempt from owner-authorized
    deletion, retention, or legal/privacy controls.

## System topology

```text
source adapters
    -> canonical raw documents and atoms
        -> Mem0 processor
              -> normal Mem0 memory/graph working state
              -> private entity-mention atoms + exact provenance + entity links
        -> Temporal History processor
             -> calendar summary atoms + intervals + coverage + lineage
        -> embedding providers
             -> versioned vectors
        -> tag enrichment
             -> catalog proposals + atom/tag evidence
    -> canonical PostgreSQL data plane
         -> raw and derived atoms
         -> provenance links and accepted state relations
         -> tag catalog and weighted associative relations
         -> immutable calibration, retrieval, interaction, and outcome events
    -> core retrieval orchestrator
         -> independent bounded candidate channels
         -> associative expansion
         -> strict temporal eligibility when requested
         -> role-aware, provenance-aware evidence packing
    -> AI consumer
         -> answer/task outcome
         -> explicit attributable feedback
         -> bounded learned-weight events
```

SQLite is the deterministic local and test adapter. PostgreSQL is the scale and online target.
The Mac is a replaceable inference worker and never the canonical data owner.

## Collective topology

The future system is one logical layered graph rather than one flat store of every user's data:

```text
global concepts, aggregated associations, public evidence, validated procedures
                                  ^
                  controlled privacy-safe promotion
                                  ^
organization/domain, project, user, and device/private evidence overlays
```

The global layer distributes learned routing capability. Private payloads, atom attachments,
queries, and identifiers remain scoped by default. An individual contribution is an immutable,
bounded observation; independent-contributor aggregation, privacy checks, held-out quality
gates, and a versioned serving snapshot stand between that observation and global behavior.

The detailed scope, learning, decay, and validation contract is authoritative in
`docs/COLLECTIVE-CAPABILITY-GRAPH.md` (ADR-0013 and ADR-0014).

## Canonical atom contract

An atom has three independent dimensions:

1. **Role** describes its epistemic position:
   - `source`: immutable evidence captured from an authoritative input;
   - `derived`: a model or processor interpretation of other atoms;
   - `interaction`: a recorded user/assistant/tool interaction;
   - `uncertainty`: an explicit unresolved or conflicting claim.
2. **Payload modality** describes what it contains or references:
   - initially `text`, with extension points for `code`, `event`, `image`, `audio`, and
     `binary_reference`.
3. **Projection kind** preserves specialized compatibility behavior such as
   `temporal_summary`. The legacy `AtomKind` field remains during migration, but it is not the
   authority for deciding whether evidence is raw or derived.

Examples:

| Atom | Role | Modality | Legacy/projection kind |
|---|---|---|---|
| Imported chat turn | source | text | source |
| Mem0 entity mention | derived | text | source during compatibility period |
| Temporal day summary | derived | text | temporal_summary |
| Assistant response | interaction | text | interaction |

Only atoms with role `source` count as canonical raw evidence. Derived atoms may be retrieved,
but claims based on them must remain traceable to supporting source atoms.

## Processor contract

Every inference processor consumes a bounded collection of canonical atom views containing:

- atom ID, namespace, content, role, modality, and source identity;
- occurrence and recording time when known;
- explicitly permitted metadata;
- processor/profile version and a deterministic request identity.

It returns zero or more artifacts/proposals containing:

- content or proposed relationship;
- role and modality;
- exact supporting atom IDs;
- provider, model, prompt, and profile versions;
- confidence and any temporal interval;
- proposed tags, entities, relations, or calibration signals;
- a deterministic completion identity.

Empty output, partial failure, retry, and replay are explicit states. A completed request is
idempotent. A profile change produces new versioned output rather than mutating old evidence.

## Capability ownership

| Responsibility | Owner | Supplier/consumer boundary |
|---|---|---|
| Raw capture, identity, and deduplication | Core | Source adapters provide bytes and source metadata |
| Canonical storage | Core | PostgreSQL online; SQLite local/test |
| Provenance and immutable event history | Core | Every processor supplies support and version data |
| Conversational fact extraction | Mem0 | Remains in Mem0 working state; not duplicated by the bootstrap |
| Private entity graph proposals | Mem0 | Core stores entity mentions, exact support, and typed atom links |
| Calendar hierarchy and summary generation | Temporal History | Core stores summaries as derived atoms |
| Coverage, temporal materialization lineage | Temporal History | Core validates and preserves the projection |
| State/reversal/conflict proposals | Processor-specific | Core owns accepted factual relations |
| Tag catalog, aliases, and hierarchy | Tags | Models and teachers may propose candidates |
| Atom/tag and tag/tag associative weights | Tags | Calibration and outcome events provide evidence |
| Embeddings | Configured provider | Core owns vector identity and storage |
| Query intent and temporal mode | Core retrieval | Processor metadata informs the plan |
| Candidate fusion and bounded traversal | Core retrieval | Tags, lexical, semantic, and relationship channels supply candidates |
| Strict current/as-of/range eligibility | Core retrieval | Temporal History supplies trusted time structure |
| Final evidence packing | Core retrieval | Raw and derived candidates compete under explicit quotas/budgets |
| Outcome learning | Core/Tags | Only attributable user or task outcomes may update learned relations |
| Global concept identity | Core/Tags governance | Private catalogs may map or propose; global promotion is separately gated |
| Scope, visibility, and contribution policy | Core | Namespace is not the security boundary; every overlay declares ownership and sharing policy |
| Collective aggregation | Future collective-learning module | Consumes minimized bounded observations; never private payloads or direct global mutations |
| Global serving snapshots | Core learning governance | Shadow evaluation, privacy/abuse gates, canary promotion, and rollback |
| Ordered procedures | Future procedure module | References validated atoms; never changes atom ordering semantics |

## Mem0 boundary

Mem0 owns its tested memory extraction and entity-relationship inference, not canonical truth or
serving retrieval. The bootstrap leaves normal `Memory.add(infer=True)` behavior intact and
projects only provenance-valid entities into Cortex. Each entity becomes a private, batch-scoped
derived atom. `SUPPORTED_BY` links point to exact source evidence, while typed Mem0 relationships
become `MEM0_ENTITY_RELATION` atom links. No evidence tags are copied onto entity atoms.

The adapter adds evidence IDs only to relationship extraction and strips them before Mem0 stores
its normal triples. Missing or invented IDs are quarantined; Cortex does not guess lineage after
the call. Mem0 availability is never required for retrieval after the projection is imported.

Replacement candidates that require evidence:

- Tags may replace parts of Mem0 entity/category retrieval if it improves constrained recall,
  explainability, and context efficiency.
- Mem0 graph extraction remains until another extractor meets the same entity, relationship,
  provenance, cost, and replay gates.

## Temporal History boundary

Temporal History owns calendar-aware materialization, coverage, lineage, resumable generation,
and pressure-compaction mechanics. It does not own generic conceptual associations or the final
answer context.

The core may use Tags to select precise pieces from the Temporal lattice and descend selected
summaries to raw support. This is a replacement candidate for Temporal History's final RAG and
frontier packing, not for its integrity machinery.

## Tags boundary

Tags owns the shared conceptual address space across raw atoms, Mem0 entities, Temporal summaries,
interactions, and future modalities. It also owns auditable learned associations created from
explicit outcomes.

Tags does not extract facts, calculate calendar periods, store procedural order, or become a
second source of truth. A tag or weight is retrieval evidence, not a factual claim.

## Retrieval stages

1. Resolve query intent, scope, and optional temporal mode.
2. Generate independent bounded lexical, semantic, and direct-tag candidate sets.
3. Expand through accepted tag relations and learned atom relations under explicit budgets.
4. Apply strict temporal eligibility only when the query requires it.
5. Pack evidence under token, role, source, diversity, and provenance constraints.
6. Record the plan, channel scores, paths, exclusions, and final atom IDs.
7. Learn only when an attributable outcome names evidence actually used.

Derived summaries and facts never receive credit as if they were raw evidence. Positive credit
may propagate through factual lineage to supporting source atoms at a reduced, explicit rate.

## Failure model

- Raw ingestion succeeds without Mem0, Temporal History, embeddings, or the Mac.
- A processor failure leaves canonical evidence unchanged and a retryable job state.
- One unavailable retrieval channel degrades explicitly and appears in diagnostics.
- No result is silently treated as successful feedback.
- No failed or partial derivation advances a completion marker.
- Provider/profile changes do not invalidate raw data or unrelated derived artifacts.

## Isolated capability gates

Combined evaluation is forbidden until each dependency has passed its own gate.

1. **Canonical core**: identity, dedupe, role/modality, provenance, transactionality, replay.
2. **Mem0 extraction**: entity/relationship precision and recall, exact provenance, changes,
   dedupe,
   resume, and cost.
3. **Temporal projection**: calendar correctness, coverage, lineage, cutoff eligibility,
   out-of-order events, compaction, and summary fidelity.
4. **Tags**: tag precision/coverage, canonicalization, alias stability, hierarchy accuracy,
   association precision, and bounded expansion.
5. **Outcome learning**: held-out improvement, collateral false positives, replay equality,
   negative bounds, and rollback.
6. **Retrieval stages**: measure lexical, semantic, tags, relationships, temporal eligibility,
   and packing independently.
7. **Pairwise integrations**: Mem0->Tags, Temporal->Tags, Mem0->retrieval, Temporal->retrieval.
8. **Full composition**: fixed processor outputs and identical budgets before scale testing.

Every experiment records the code revision, dataset checksum, storage adapter, processor/model
versions, prompts/profiles, feature switches, token budget, random seed, latency, and artifact
location.

## Replacement rule

A native capability replaces an upstream component only when a matched-input, matched-budget
test shows that it is better on the metric the component owns, while preserving the component's
required invariants. Architectural preference alone is not evidence.

Replacement is performed at the narrowest boundary. For example, Tags may replace Temporal
answer-context selection while Temporal History continues to own calendar materialization and
lineage.

## Implementation order

1. **Implemented:** add canonical evidence role and payload modality while retaining legacy kind
   compatibility.
2. **Implemented:** project Mem0 entities as private, tagless atoms with role `derived`.
3. **Implemented:** define and validate endpoint-level source lineage for Mem0 relationships.
4. **Implemented:** add role/provenance-aware evidence packing.
5. **Implemented (contract level):** build deterministic isolated capability fixtures and
   reports. Real-model quality gates remain required before pairwise testing.
6. **Implemented (contract level):** restore and gate natural-language query -> generated
   concept -> canonical tag -> atom retrieval in normal serving and evaluation paths.
7. **Implemented:** structured tag candidates, canonical-only serving, and atomic
   promote/merge/reject lifecycle with legacy migration.
8. **Implemented:** immutable event-backed edge weights, migration baselines, replay audit, and
   explicit aggregate-cache repair.
9. **Accepted (contract level):** restore the privacy-preserving collective graph as a top-level
   scope, including global concepts and private evidence overlays.
10. **Accepted (contract level):** restore non-negative unbounded support, relative serving
    influence, passive decay, active negative learning, dormancy, and reactivation.
11. Record the payload-reference/handler contract with scope, visibility, and contribution
    policy without disrupting text ingestion.
12. Run real-model quality gates and then pairwise integrations.
13. Run the isolated cross-user transfer/leakage experiment before building global
    infrastructure.
14. Decide which upstream serving functions Tags has earned the right to replace.
15. Run scale probes before global deployment, then add procedures only through their own
    validation gate.

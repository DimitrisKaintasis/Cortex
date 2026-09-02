# Capability preservation and implementation plan

> Architectural authority: [`ARCHITECTURE.md`](ARCHITECTURE.md) and ADR-0009 define current
> component ownership, processor contracts, and testing order. This document remains the
> historical capability register and roadmap; conflicting boundary guidance is superseded.

- Status: Proposed for review
- Date: 2026-08-20
- Current repository baseline: `6659b6f`
- Cortex source reviewed: `DimitrisKaintasis/DebUI` commit
  `dae77ea060c5cc135aa398cb091adf8a499d722c`

## Purpose

This plan prevents useful behavior from disappearing while Data Retrieval keeps the small
atom-centered architecture chosen for the migration. It covers the original Tags design,
DevUI research, DebUI/Cortex, Temporal History, Mem0, and the capabilities already implemented
in this repository.

The preservation rule is:

> Preserve a capability's purpose, evidence, and extension point. Do not preserve an old
> implementation choice merely because it carried that capability first.

This means Cortex is not treated as either a package to copy wholesale or a collection of
side features. Its retrieval, learning, grouping, exploration, integration, security,
operations, and evaluation work all remain visible. They enter the implementation in the
order required to make them correct and measurable.

## Status language

Every capability must use one of these states:

1. **Operational**: implemented in Data Retrieval and covered by representative tests.
2. **Prototype evidence**: Cortex contains executable behavior, but it needs redesign,
   hardening, or better evaluation before migration.
3. **Decision-locked design**: accepted in the historical design, but Cortex's documentation
   was ahead of its runtime implementation.
4. **Research hypothesis**: worth testing, but not yet accepted as ranking or learning law.
5. **Superseded implementation**: its purpose remains, but its particular storage, framework,
   or formula is not the target.

“Deferred” is never a synonym for rejected.

## What Cortex actually contributed

Cortex contains four distinct layers:

1. A working retrieval-service prototype:
   - HTTP contracts, API-key scopes, limits, trace IDs, and redacted diagnostics;
   - graph, lexical, and semantic retrieval with bounded candidate pools;
   - channel degradation, hard request budgets, caching, and explainable scores;
   - interaction ingestion, bounded feedback, residual updates, and event records;
   - scheduled maintenance, audit logging, webhook retry, and a dead-letter queue.
2. Executable tag-memory experiments:
   - LLM proposals guarded by deterministic teachers;
   - strict catalog-first matching at `0.90`;
   - tag-pair weights and GroupTag persistence;
   - pair/group context boosts, adjacency bonuses, RoC diagnostics, and semantic fail-safe;
   - hot/warm/cold tiering and epsilon-greedy exploration.
3. Decision-locked designs whose runtime was partial:
   - broad/specific tag semantics and alias/proposal history;
   - emergent groups with cohesion, promotion, demotion, and hysteresis;
   - lineage-rich tag-pair/group evolution;
   - adaptive channel weights learned under shadow gates and rollback;
   - negative direct-plus-one-hop forgetting and buffered conflict handling.
4. An engineering control system:
   - schema and API evolution rules;
   - benchmark datasets, statistical gates, evidence packs, and test stages;
   - migration, deployment, rollback, backup/restore, incident, privacy, and security
     procedures;
   - a traceability matrix and change-impact navigation.

Those layers have different migration requirements. A prototype is evidence that an idea is
useful and testable; it is not proof that its current formula or storage path is production
ready.

## Important findings from the Cortex runtime

The plan must preserve Cortex's intent while accounting for what its code actually does:

1. Tag-pair and GroupTag storage are present, including migration `005_group_tags.cypher`,
   ingestion and interaction updates, retrieval boosts, DTOs, and admin reads. They are not
   merely documentation.
2. The implemented groups are deterministic sets of the first five sorted tags. They do not
   yet implement the documented rolling-window discovery, cohesion-based promotion/demotion,
   or hysteresis. They prove the storage and retrieval seam, not emergent grouping quality.
3. Cortex's “RoC” implementation finds a score cliff in an already ranked list. It is not the
   original recursive tag-layer expansion with a rate-of-change stop rule.
4. Its adjacency implementation boosts neighbors already in the candidate set. It does not
   fully recover missing adjacent atoms. Data Retrieval's calibrated `ADJACENT_TO` expansion is
   closer to the original recovery purpose.
5. Channel-weight snapshots, bounds, and rollback records exist, but the scheduled reconcile
   submits the current weights rather than deriving improved weights from outcomes. The safety
   shell is useful; the optimizer remains to be built.
6. Several learning states are process-local in Cortex: tier hysteresis, conflict buffers,
   namespace counters, queues, and parts of the Neo4j adapter's update state. The intended
   capabilities require durable PostgreSQL state before they can be trusted across restarts or
   multiple workers.
7. Cortex's Mem0 bridge imports native atoms, deduplicates, records batch lineage, and boosts
   interaction learning when Mem0 was used. Data Retrieval's newer bridge adds chronological
   reverse ingestion, stable completion markers, replay-safe calibration signals, derived roles,
   and fact-level `SUPPORTED_BY` lineage. Full batch membership remains separate audit metadata.
8. Cortex's benchmark harness is valuable infrastructure, but its committed 300-atom/900-query
   dataset is synthetic and its recorded direct-tag baseline is zero. Its passing result proves
   harness execution and regression protection, not general long-memory quality.
9. The fixed Neo4j vector dimensions and single-node service were appropriate prototype
   choices. The current PostgreSQL adapter's per-model vector identity and bounded indexed
   candidates are the better fit for the long-term storage vision.

## Target architecture

Keep one canonical data plane and add capabilities as modules around it:

```text
Source adapters
    -> immutable documents and atoms
    -> optional enrichment jobs
         -> tags and catalog decisions
         -> embeddings
         -> Mem0-derived atoms
         -> Temporal-derived atoms
    -> PostgreSQL canonical state
         -> atoms and payload references
         -> tags, weighted relationships, and groups
         -> provenance and immutable weight events
         -> retrieval, interaction, feedback, and job events
    -> staged retrieval
         -> tag + lexical + semantic seeds
         -> bounded associative expansion
         -> conditional temporal interpretation
         -> diversity/evidence packing
    -> consumers
         -> CLI
         -> remote worker
         -> future HTTP/Codex integration
         -> future procedure/skill add-on
```

SQLite remains the deterministic local/test adapter. PostgreSQL remains the scale and online
target. Temporal History and Mem0 remain replaceable processors. The Mac remains an inference
and worker host, not canonical storage.

## Core model decisions to make first

### 1. Separate semantic role from payload modality

**Recommendation:** replace the current overloaded closed `AtomKind` meaning with two
orthogonal concepts while keeping the atom itself small:

- role: source, derived, interaction, uncertainty, and later procedure;
- payload modality: text, code, event, image, audio, binary reference, or MIME-like value.

The atom keeps a compact searchable text projection plus an optional external payload
reference. Add-on-specific details stay in metadata or an add-on table.

**Alternative:** keep adding enum values such as `MEM0_MEMORY`, `CODE`, `IMAGE`, and
`PROCEDURE`. This is initially easy but confuses what an atom means with what it contains and
makes every new source format a core schema change.

**Downstream impact:** this is the prerequisite for universal atoms, code/event adapters,
correct derived-evidence packing, future multimodal ingestion, and procedure atoms.

### 2. Use one immutable event ledger for every learned weight mutation

**Recommendation:** generalize the current `CalibrationSignal` idea into an append-only weight
event contract. Each event identifies the target edge/node, delta or initialization evidence,
provider/profile version, retrieval/interaction/outcome IDs, source lineage, and event time.
Serving tables keep current aggregate weights for speed; aggregates can be replayed from events.

**Alternative:** continue storing only the latest edge weight plus a bounded list of evidence
IDs. This is smaller but cannot faithfully reconstruct tag-pair history, group evolution,
conflict handling, rollback, or temporal learning analysis.

**Downstream impact:** one event model supports teacher calibration, Mem0 calibration, user
feedback, group evolution, negative forgetting, adaptive retrieval profiles, and audit without
creating a different update system for each feature.

### 3. Keep group discovery separate from atoms and procedures

**Recommendation:** represent a group as a first-class associative object with weighted tag
membership and lineage. It may be addressable through a native tag, but it is not a source atom
and it does not encode order. Promote a group only after a windowed cohesion/support gate;
demote it with hysteresis rather than deleting it.

**Alternatives:** deterministic sorted-tag groups, which create stable IDs but not meaningful
emergence; or treating every group as an atom, which makes retrieval convenient but mixes
evidence with a learned index structure.

**Downstream impact:** GroupTags can improve context expansion and diversity while the separate
procedure structure remains free to represent ordered steps later.

### 4. Preserve Cortex controls at the boundary where they become necessary

**Recommendation:** keep the library/CLI while behavior is evolving, then add a small service
and durable PostgreSQL job queue when the Mac or Codex needs remote access. At that boundary,
activate scoped authentication, concurrency/rate limits, hard time budgets, health, redacted
traces, audit events, retries, and recovery procedures together.

**Alternatives:** add the full Flask/APScheduler/webhook surface immediately, increasing moving
parts before a network consumer exists; or omit the controls until after exposure, creating an
unsafe and difficult-to-operate service.

**Downstream impact:** remote execution becomes restart-safe and observable without making an
in-process scheduler or local JSONL queue part of canonical state.

## Capability register

| Capability | Purpose that must survive | Cortex evidence | Data Retrieval state | Planned disposition |
|---|---|---|---|---|
| Universal atoms | Store heterogeneous knowledge behind one retrieval identity | text/code/event DTOs, payload refs, code parser | text content with role-like `AtomKind` | Split role/modality; add payload reference contract |
| Raw-before-derived ingestion | Capture data even when AI is unavailable | degrade/backfill and pending queue paths | operational and atomic | Keep; add durable jobs rather than coupling capture to inference |
| Stable identity/dedupe | Replay safely at very large scale | UUID plus scoped content hash | deterministic IDs, staged ingestion, exact dedupe | Keep current implementation |
| LLM + deterministic teachers | Use models for semantics and deterministic evidence for structure/cold start | tag generator, teacher hints, AST tags | AI tag enrichment plus replayable teacher calibration | Keep; strengthen source-specific teachers |
| Catalog-first tags | Prevent vocabulary fragmentation | strict `0.90` matching and proposal state | operational exact/semantic matching | Keep; add alias/promotion history and quality gates |
| Broad/specific hierarchy | Move between concepts and details | DTO/spec plus partial inference | lexical `parent_of` calibration | Replace lexical-only rule with evidence-backed hierarchy candidates |
| Tag-pair weights | Preserve the original associative address space | persisted `RELATED_TO`, ingest/interaction updates, score boost | operational `co_occurs` and `parent_of` | Move all updates to immutable weight events; retain aggregates |
| Emergent GroupTags | Represent stable higher-order contexts | persistence and retrieval seam; discovery incomplete | accepted, not implemented | Implement after event ledger with window/cohesion/hysteresis gates |
| Source adjacency | Recover context around a strong atom | candidate-only bonus | calibrated `ADJACENT_TO` expansion | Keep current behavior; measure source-type-specific windows |
| Recursive graph expansion | Discover related concepts beyond direct tags | decision-locked adjacency/RoC target | one-hop tag and `CO_USED` expansion | Add bounded layered expansion with budgets and visited-set controls |
| RoC stopping | Stop traversal when marginal evidence collapses | ranked-list score-cliff prototype | absent | Implement as traversal-layer gain stop; compare with fixed depth |
| Fail-safe scan | Broaden search when the normal path is weak | expanded semantic fill | absent | Add after confidence calibration; record trigger and added evidence |
| Hybrid retrieval/degradation | Continue when one provider fails | parallel channels, retry, degrade diagnostics | staged channels; semantic degrades | Preserve; add per-channel budgets when service concurrency exists |
| Temporal retrieval | Prefer current/as-of/history evidence only when requested | recency modifier only | Temporal History projection and conditional lens | Keep current design; do not restore unconditional freshness scoring |
| Gravity signals | Model momentum, importance, connectivity, and explicit user will | velocity/mass/centrality/user-will formula | partial equivalents in weights, time, relationships | Preserve as separately named features and ablate; avoid one opaque score |
| Reliability/confidence | Separate evidence trust from relevance | per-atom fields and modifiers | relationship confidence plus low-confidence output | Define provenance-derived reliability and model confidence separately |
| Exploration/tiering | Prevent popular memories from monopolizing retrieval | hot/warm/cold and epsilon sampling | accepted, not implemented | Add after durable outcome counters; gate usefulness and novelty together |
| Global/project/user scopes | Reuse shared knowledge while preserving local context | equal global/project blend and Mem0 user facts | namespace isolation | Design explicit scope policy; no hard-coded equal blend |
| Result diversification | Avoid redundant summaries/groups crowding raw evidence | group-diversity target | temporal-summary dedupe only | Add role/source/group quotas and near-duplicate packing |
| Outcome learning | Improve graph only from attributable use | feedback kinds and interaction-derived events | operational selected-evidence feedback | Keep; expand event types and durability |
| Negative forgetting | Reduce harmful paths without deleting evidence | direct-one-hop policy and conflict buffer | direct negative updates only | Implement buffered direct-plus-one-hop events with bounded propagation |
| Residual updates | Preserve excess deltas when safety caps apply | residual queue and reconcile job | bounded update, no residual queue | Add only if real caps regularly defer meaningful evidence |
| Adaptive channel profiles | Learn retrieval mixes without unsafe global mutation | snapshots, guards, rollback shell | static channel rule | Build offline/shadow optimizer; promote versioned profiles after gates |
| Mem0 bootstrap | Distill and initially calibrate useful memory | import, dedupe, batch lineage, boost | stronger replayable reverse bridge | Fix derived roles and support lineage; retain Mem0 as teacher, not serving DB |
| First-class interactions | Capture the evidence-to-outcome loop | interaction DTO, atom, queue | operational interaction atoms and evidence lineage | Keep; add durable event schema and completion semantics |
| Durable work scheduling | Let Mac continue when laptop is off | APScheduler, pending queue, backfills | synchronous CLI only | PostgreSQL jobs/leases after hosted DB; Mac runs worker process |
| Service/API boundary | Integrate host apps and eventually Codex | versioned HTTP API and error contracts | library/CLI | Add minimal API after jobs and hosted database are ready |
| Auth/limits/privacy | Safely expose memory operations | scopes, anomaly hook, rate/concurrency limits, redaction | local-only operational model | Required in same milestone as any network service |
| Audit/traceability | Explain results, mutations, and operations | trace IDs, audit JSONL, events | retrieval/feedback/calibration records | Unify trace IDs and immutable events; define redaction |
| Webhook/DLQ | Deliver operational alerts reliably | retries, idempotency, persistent DLQ | absent | Preserve interface; implement only when a real receiver exists |
| Backup/migration/rollback | Recover canonical memory safely | detailed runbooks and forward-fix policy | schema initialization and Docker dev setup | Required before hosted activation; test restoration, not only backup |
| Evaluation governance | Prevent attractive formulas from silently regressing behavior | matrices, gates, evidence packs, dataset manifest | unit tests, small corpus, LongMemEval pipeline | Adopt layered real benchmarks and versioned experiment reports |
| Procedure/skill synthesis | Turn repeated useful evidence into reusable problem-solving guidance | legacy future-proof lineage and habit-correcting idea | intentionally separate | Build only after retrieval/learning evidence is trustworthy |

## Dependency-ordered implementation roadmap

### Phase 0 — Freeze the truth before adding ranking behavior

Goal: make the current capabilities and limitations reproducible.

1. Keep this capability register and the context ledger as review authorities.
2. Add a compact traceability matrix from capability to code, schema, test, and benchmark.
3. Record current SQLite/PostgreSQL unit and integration results.
4. Record current project-history and selected LongMemEval results with exact dataset/model
   identities.
5. Stop committing enormous per-query benchmark dumps by default; retain summaries,
   configuration, checksums, and separately stored detailed artifacts.

Exit gate: another agent can identify whether a behavior is operational, proposed, or only
historical without rereading all old repositories.

### Phase 1 — Repair evidence semantics and the universal atom boundary

Goal: make existing ingestion safe to extend.

1. Split atom role from payload modality and add optional payload references/text projections.
2. Mark Mem0 outputs and all future model outputs as derived evidence rather than raw source.
3. Replace batch-wide “all sources support every fact” lineage with support selected by Mem0
   output metadata or a bounded alignment pass. Preserve batch membership separately.
4. Add role-aware result packing:
   - minimum raw-source quota;
   - caps for derived summaries/memories;
   - near-duplicate collapse across all derived roles;
   - clear `current`, `historical`, `continuity`, and `derived` labels.
5. Add tests proving derived evidence cannot impersonate source truth.

Exit gate: the complete current pipeline can ingest, distill, temporally summarize, retrieve,
and explain a result without provenance ambiguity or derived-evidence crowding.

### Phase 2 — Make all weight changes replayable

Goal: establish the learning substrate before adding more learners.

1. Introduce the immutable weight-event schema and aggregate update transaction.
2. Migrate teacher calibration and Mem0 calibration to that schema.
3. Migrate outcome feedback, `CO_USED`, atom-tag, and tag-pair changes to the same schema.
4. Add durable conflict observations and confirmation windows.
5. Add replay, audit, and aggregate-rebuild tests on SQLite and PostgreSQL.
6. Preserve raw non-negative unbounded positive and negative support; normalize only at
   retrieval time inside explicit relation/scope/query comparison neighborhoods.
7. Treat current aggregate weights as the local compatibility projection. Collective learning
   adds immutable observations, rebuildable support aggregates, and versioned serving snapshots.

Exit gate: deleting and rebuilding aggregate weights from events produces the same serving
state, and repeated event IDs cannot double-apply.

### Phase 3 — Finish ingestion as an extensible system

Goal: support large heterogeneous corpora without changing the atom core for each source.

1. Define a source-adapter contract that emits documents, ordered atoms, timestamps,
   participants, references, explicit factual links, and payload metadata.
2. Keep LongMemEval as the reference chat adapter.
3. Add WikiConv and EverMemBench adapters without label leakage.
4. Add code ingestion using a language-neutral skeleton contract; start with Python AST and
   preserve definitions, calls, imports, files, and exact source locations as structural
   evidence.
5. Add Git-history and generic JSONL adapters after the contract is stable.
6. Version every parser, tagger, embedder, and projection so enrichment can be replayed.

Exit gate: two structurally different sources can pass through the same raw/enrichment/
retrieval path while preserving their source-specific facts.

### Phase 4 — Restore the full associative retrieval potential

Goal: make the tag graph the primary semantic address space rather than a small score bonus.

1. Implement bounded tag-layer expansion with:
   - relation-type-aware traversal;
   - visited sets and per-layer budgets;
   - explicit evidence paths;
   - fixed-depth baseline and RoC layer-gain stopping experiment.
2. Add semantic fail-safe only for low-confidence/low-coverage results.
3. Implement evidence-backed broad/specific hierarchy candidates and promotion gates.
4. Implement GroupTags using rolling support, cohesion, lineage, promotion/demotion, and
   hysteresis. Compare their value against ordinary tag-pair traversal.
5. Add result diversification across raw/derived roles, sources, time periods, and groups.
6. Implement the ADR-0013 identity seam: globally stable concepts plus organization, project,
   user, and private evidence overlays. Experiment with scope blending as profiles rather than a
   fixed 50/50 rule.
7. Keep the temporal lens conditional. Later compare a semantic-temporal beam with the staged
   lens; do not replace the proven staged path without an as-of/current-state win.

Exit gate: each added traversal mechanism improves a representative benchmark slice or remains
disabled behind a versioned profile.

### Phase 5 — Restore controlled evolution and exploration

Goal: let the system improve without becoming self-confirming or destructive.

1. Add negative direct-plus-one-hop propagation with conflict confirmation and daily bounds.
2. Add durable namespace maturity counters based on retrievals and attributable outcomes.
3. Add hot/warm/cold tiering and exploration with persisted hysteresis.
4. Evaluate novelty together with answer usefulness; never pass exploration merely because a
   cold item appeared.
5. Build an offline/shadow channel-profile optimizer from outcome events.
6. Store profile snapshots, compare against a stable baseline, promote through a guarded canary,
   and automatically roll back repeated regressions.
7. Evaluate gravity components separately:
   - velocity or recent useful activity;
   - mass or accumulated support;
   - centrality;
   - explicit user will;
   - reliability.
   Only retain components that add measurable value without violating temporal semantics.
8. Implement ADR-0014 as shadow profiles before serving mutation:
   - unbounded positive/negative support;
   - proportional lazy decay;
   - relative `log1p` neighborhood normalization;
   - hot/warm/cold/dormant/inhibited lifecycle;
   - reactivation and snapshot rollback.
9. Preserve the original bounded discrete decay, no-passive-decay, and proportional half-life
   policies as matched-input controls.

Exit gate: learning survives restart, is reproducible from events, has a stable control group,
and cannot reinforce an item merely because the system returned it.

### Phase 5A — Prove privacy-preserving collective transfer

Goal: validate the project's central thesis before building global production infrastructure.

1. Create A and B with disjoint private atoms mapped to a controlled shared concept catalog.
2. Establish B's held-out retrieval and task baseline.
3. Produce attributable successes and failures using only A.
4. Export only policy-compliant bounded concept-relationship observations; export no atom,
   document, query, session, or public user identity.
5. Aggregate a shadow global snapshot using independent-contributor limits.
6. Re-evaluate B for task lift, recall, context cost, false positives, and unrelated regression.
7. Probe reconstruction of A's content, identity, rare concepts, and source structure.
8. Add stale, noisy, repetitive, and malicious contributors; test decay, thresholds, canary,
   and rollback.
9. Repeat with a stronger teacher and a smaller consumer model.

Exit gate: B improves through A's outcome observations without receiving A's private evidence,
privacy leakage remains below the accepted threshold, and one contributor cannot materially
poison shared serving behavior.

### Phase 6 — Make the Mac an autonomous worker and expose safe integrations

Goal: run enrichment while the laptop is off without moving canonical data onto the Mac.

Prerequisite: hosted PostgreSQL or another always-reachable canonical database with TLS,
network restrictions, backups, and tested restoration.

1. Add a PostgreSQL-backed job table with leases, heartbeats, attempts, backoff, cancellation,
   and idempotent completion markers.
2. Run one small worker process on the Mac for tag, embedding, Mem0, Temporal, and later
   evaluation jobs. Concurrency and model selection remain configuration.
3. Add health and structured logs showing queue depth, active job, model, duration, and failure
   stage without logging sensitive content.
4. Add a minimal versioned API for submit/status/retrieve/feedback.
5. In the same release, add scoped credentials, request limits, hard time budgets, redacted
   diagnostics, trace IDs, and audit events.
6. Add deployment, migration, rollback, backup/restore, and incident procedures tested against
   the actual hosted topology.
7. Add webhook/DLQ delivery only when there is a real operational receiver.

Exit gate: a job submitted from the laptop can complete on the Mac after the laptop disconnects,
with canonical results, progress, retry history, and logs visible when it reconnects.

### Phase 7 — Add the procedure and small-model enhancement layer

Goal: turn repeated successful evidence into validated, reusable task guidance.

1. Keep associative atoms and weights unchanged.
2. Define a separate procedure object with ordered steps, prerequisites, branches, tools,
   expected outcomes, failure modes, and source atom lineage.
3. Propose procedures from repeated successful interaction paths and useful atom/group patterns.
4. Validate them through replay or task outcomes before promotion.
5. Retrieve procedures as structured guidance for small models while also supplying supporting
   source atoms.
6. Train or evaluate small models on validated structures, not hidden chain-of-thought traces.
7. Preserve counterexamples and negative outcomes so the system can retrieve corrective
   patterns when a known anti-pattern reappears.

Exit gate: a smaller model completes selected tasks more reliably with retrieved procedures
than with the same token budget of unstructured memories.

## Evaluation program

Use separate gates because one metric cannot validate this system:

1. Ingestion gates:
   - deterministic replay and restart safety;
   - zero label leakage;
   - source/derived role correctness;
   - lineage precision;
   - bounded memory and transaction sizes.
2. Tag gates:
   - precision, coverage, catalog-match correctness, alias stability;
   - broad/specific relation accuracy;
   - per-language and per-payload slices.
3. Retrieval gates:
   - hit/recall and ranking metrics;
   - raw evidence coverage and redundancy;
   - current/as-of/range/history accuracy;
   - traversal path explainability;
   - provider degradation behavior and latency.
4. Learning gates:
   - attributable improvement over a frozen baseline;
   - replay equality and idempotency;
   - no-delete invariants;
   - bounded negative propagation;
   - rollback under repeated regression.
5. Exploration/group gates:
   - useful novelty, not selection share alone;
   - group stability and cohesion over time;
   - retrieval gain over tag pairs without groups.
6. Procedure/small-model gates:
   - task success, cost, latency, and context-token efficiency;
   - source support and procedure validity;
   - improvement over unstructured retrieval.

Dataset order:

1. checked-in deterministic regression corpus;
2. project-history current/as-of acceptance set;
3. selected LongMemEval development slice;
4. frozen LongMemEval evaluation slice;
5. WikiConv for conversation evolution and conflict/supersession;
6. EverMemBench for broad long-term memory behavior;
7. private real-world corpus kept outside git.

Every experiment records code commit, dataset identity/checksum, storage adapter, model/provider,
prompt/profile version, parameters, random seed where relevant, result summary, and artifact
location.

## Immediate implementation order

The next work should be narrow even though the preserved vision is broad:

1. **Implemented:** split role/modality and correct Mem0-derived atom classification;
2. **Implemented:** add provenance-aware and role-aware evidence packing;
3. **Implemented (contract level):** restore and gate the optional generated-query-tag path;
4. **Implemented:** structured tag proposals and quarantined promotion/merge/reject lifecycle;
5. **Implemented:** unified immutable weight-event ledger and aggregate audit/rebuild;
6. **Accepted (contract level):** ADR-0013 collective graph and ADR-0014 unbounded support,
   relative influence, and forgetting lifecycle;
7. record the payload-reference/handler contract with scope, visibility, and contribution
   policy;
8. rerun real-model capability gates, then pairwise integrations and a fixed LongMemEval slice;
9. run the isolated cross-user transfer/leakage/poisoning experiment;
10. then choose among source adapters, bounded recursive traversal, and GroupTags as the next
    measured capability.

This order protects current data before adding ranking complexity. It also gives GroupTags,
recursive traversal, exploration, adaptive profiles, remote workers, and procedures the durable
evidence substrate each one needs.

## Remaining review questions

1. Which payload-reference and handler representation best preserves the accepted scope and
   visibility contract?
2. Should group membership stay a separate associative structure or become group-as-atom after
   matched evaluation?
3. Which passive-decay, maturity, negative-penalty, and robust normalization profiles win the
   required ablations?
4. What consent, sensitivity, minimum-contributor, secure-aggregation, and leakage thresholds
   are required before a private observation can contribute globally?
5. Which hosted PostgreSQL topology and backup/restore gate precede autonomous Mac work?

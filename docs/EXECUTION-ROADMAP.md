# Execution Roadmap

Status: active  
Last updated: 2026-09-02  
Current step: 4 — expand the policy comparison fixture matrix

This is the operational source of truth for what we build next. The architecture documents
describe what the system may become; this file records the order in which we will prove and
implement it. Update the status, evidence, and decision log whenever a step changes state.

## Working rule

We will validate the riskiest claim with the smallest faithful implementation before building
production infrastructure around it. The next implementation is therefore an isolated,
deterministic `collective-transfer-v1` experiment. It must not mutate normal retrieval behavior,
existing databases, or global serving state.

Status values are `not started`, `in progress`, `blocked`, `passed`, `failed`, and `deferred`.
A step is `passed` only when its evidence is checked into the repository or linked below.

## Milestone map

| Step | Status | Outcome | Unlocks |
|---|---|---|---|
| 0. Preserve baseline | passed | Accepted scope and current behavior are recoverable | Safe experimentation |
| 1. Reproduce current baseline | passed | Existing tests and capability gates have recorded results | Trustworthy before/after comparison |
| 2. Build shadow collective core | passed | Policy math runs without production mutation | Cross-user experiments |
| 3. Prove positive and negative transfer | passed | A changes B through shared concepts only | Central thesis evaluation |
| 4. Compare weight policies | in progress | A measured policy wins over controls | Candidate learning policy |
| 5. Test privacy, poisoning, and lifecycle | in progress | Leakage and manipulation stay within declared gates | Safe schema design |
| 6. Make the architecture decision | not started | Proceed, revise, or reject is recorded from evidence | Production implementation |
| 7. Repair canonical contracts | not started | Scope, visibility, payload, and contribution are explicit | Real multi-scope runtime |
| 8. Validate with real models and data | not started | Gains survive realistic noise and model differences | Product evidence |
| 9. Add production collective storage | not started | Events, projections, snapshots, and rollback are durable | Hosted collective service |
| 10. Make the Mac autonomous | not started | Remote jobs continue while the laptop is offline | Continuous enrichment/evaluation |
| 11. Add procedures | deferred | Validated ordered guidance improves small models | Small-model enhancement layer |

## Step 0 — Preserve the accepted baseline

Purpose: prevent the vision, historical formulas, and present implementation boundary from
being reconstructed from chat again.

- [x] Record the collective capability graph and privacy boundary.
- [x] Record unbounded support, relative influence, passive decay, inhibition, and reactivation.
- [x] Preserve both the original bounded Tags formula and later Cortex formula as controls.
- [x] Record current runtime mismatches and non-goals.
- [x] Create this ordered roadmap.
- [x] Review the documentation diff for contradictions.
- [x] Commit the documentation baseline separately from experimental code (`77b3df4`).

Exit gate: the accepted architecture, historical provenance, next experiment, and known gaps are
available from repository documents in a clean documentation commit.

Evidence: `COLLECTIVE-CAPABILITY-GRAPH.md`, ADR-0013, ADR-0014, the context ledger, and this file.

## Step 1 — Reproduce the current baseline

Purpose: distinguish new experimental effects from regressions in the working ingestion,
retrieval, temporal, Mem0, tag-lifecycle, and weight-ledger foundations.

- [x] Run the full unit test suite.
- [x] Run all seven isolated capability gates.
- [x] Run lint and documentation integrity checks.
- [x] Record the commit, commands, environment, pass/fail counts, and artifacts.
- [x] Explicitly label `GlobalAblationRunner` as a shared-namespace LongMemEval ablation, not a
  privacy-preserving collective-transfer test.

Exit gate: the baseline is reproducible, and any existing failure is documented before new code
is introduced.

Evidence recorded on baseline commit `77b3df4` with Python 3.13.1:

- 97 unit tests passed; 2 live PostgreSQL integration tests skipped because no test DSN was set;
- 7 of 7 isolated capability gates passed;
- `src` and `tests` passed Ruff 0.15.1;
- whole-repository Ruff found 53 pre-existing findings in legacy benchmark scripts, primarily
  long lines, unused imports/variables, and unnecessary f-strings;
- `git diff --check` passed.

## Step 2 — Build the shadow collective core

Purpose: implement only the concepts required to test the central thesis. This is an
experimental policy module, not the production global graph.

- [x] Define stable shared concept IDs independent of A and B's private namespaces.
- [x] Define a minimized relationship-observation record containing no atom payload, document,
  query, session, or public user identity.
- [x] Keep positive and negative support as separate values.
- [x] Implement configurable lazy proportional half-life decay.
- [x] Implement explicit comparison neighborhoods and relative `log1p` normalization.
- [x] Add contributor caps and idempotency so repetition by one contributor is bounded.
- [x] Aggregate observations into an immutable shadow snapshot.
- [x] Make every result deterministic and replayable from observations.
- [x] Keep the normal repositories, database schema, and `RetrievalService` unchanged.

Exit gate: identical observation input produces an identical snapshot and score output across
replay, while existing tests remain unchanged.

## Step 3 — Prove positive and negative cross-user transfer

Purpose: test whether the architecture produces value that isolated Mem0 or Temporal History
does not provide by itself.

Fixture:

- A and B have disjoint private atoms.
- Their atoms map to some shared global concepts.
- B has held-out queries/tasks with known relevant evidence.
- A's successful and failed outcomes generate only relationship observations.

Required assertions:

- [x] B's relevant retrieval improves after A's successful observations.
- [x] A verified failure weakens the corresponding route for B.
- [x] B never receives A's atoms, text, metadata, source structure, or identity in the fixture.
- [x] Unrelated B queries do not regress beyond the declared tolerance.
- [x] Removing the global snapshot restores B's exact baseline.
- [x] The report shows the route and score components that caused every change.

Initial gates for the deterministic fixture:

- target evidence rank strictly improves in the positive case;
- target evidence rank strictly worsens or its influence falls in the negative case;
- zero private-payload or private-identifier crossover;
- zero change to unrelated control queries;
- byte-equivalent results for repeated replay with the same inputs.

Exit gate: the test demonstrates capability transfer through shared relationships rather than
shared private evidence.

Evidence: `COLLECTIVE-TRANSFER-EXPERIMENT.md`, `evals/collective_transfer_v1.json`, and
`tests/test_collective.py`. The deterministic suite passes 18 of 18 checks. This gate proves the
mechanism in the controlled fixture, not real-world quality or production privacy.

## Step 4 — Compare learning policies

Purpose: choose math from measurements instead of inheriting constants by intuition.

Run the same fixture and inputs against:

1. no collective learning;
2. original bounded `0..1` Tags updates;
3. unbounded support without passive decay;
4. unbounded support with proportional half-life decay;
5. unbounded, decayed, separate positive/negative support;
6. maximum-based relative `log1p` normalization;
7. a robust-reference alternative such as a percentile, if maximum-based normalization is
   unstable.

Measure target rank/recall, unrelated regression, response to negative evidence, replay
equality, time-to-dormancy, reactivation, contributor concentration, and numerical stability.

Selective review cascade implemented at the deterministic contract level:

- [x] Add a cheap, payload-free uncertainty/impact/risk triage gate.
- [x] Hold sensitive and high-risk updates while the active snapshot continues serving.
- [x] Add asynchronous review priority and a bounded review-budget planner.
- [x] Store structured AI verdicts without payloads or hidden reasoning.
- [x] Keep behavioral and AI-review support in separate aggregate channels.
- [x] Bound AI influence by confidence, proposed strength, reviewer reliability, reviewer-family
  cap, and aggregation policy.
- [x] Compare cheap-only, review-everything, and gated review on one deterministic fixture.
- [ ] Repeat the comparison with ambiguous threshold cases and real local/strong models.

Automatic cheap-feature calculation implemented at the deterministic contract level:

- [x] Derive maturity, conflict, concentration, rarity, and novelty from ledger aggregates.
- [x] Use cached calibrated vector similarity, top-two ambiguity, novelty, and duplicate signals.
- [x] Use Mem0 supporting/conflicting facts while deduplicating common source lineage.
- [x] Use Temporal currentness, conflict, and supersession.
- [x] Use an ephemeral active-versus-candidate impact contract.
- [x] Preserve provider/profile provenance and explanations for every component.
- [x] Degrade safely when processors are missing and export no contributor/lineage identifiers.
- [x] Connect extracted features directly to the cheap review triage contract.
- [x] Implement a read-only repository adapter that populates evidence from real stored data.
- [ ] Calibrate vector similarity and triage thresholds on development data.

Evidence: `REVIEW-CASCADE-EXPERIMENT.md`, `evals/review_cascade_v1.json`, and
`tests/test_review_cascade.py`. The cascade matched 13/13 fixed review decisions while escalating
3/13 cases and consuming about 24% of review-everything relative cost. This proves orchestration,
not real reviewer quality or production thresholds.

Exit gate: one candidate improves the target behavior without relying on raw cross-neighborhood
weight comparison or introducing unacceptable regression. If none does, revise the hypothesis
before modifying production code.

## Step 5 — Test privacy, poisoning, and lifecycle

Purpose: make failure modes visible before we encode the global storage contract.

- [ ] Try to infer A's content, identity, rare concepts, and source structure from exports.
- [x] Repeat identical feedback from one contributor and verify the cap.
- [x] Add multiple independent contributors and verify their evidence can accumulate.
- [ ] Add noisy, stale, contradictory, and malicious contributors.
- [x] Verify dormancy through time, inhibition through verified negative evidence, and
  reactivation through renewed independent evidence.
- [ ] Build shadow and candidate snapshots; reject a regression and restore the active snapshot.
- [x] Record residual risks that require consent, sensitivity classification, thresholding,
  aggregation, or stronger privacy technology in production.

Exit gate: the synthetic adversarial suite passes explicit thresholds, or the failed mechanism
is revised and rerun. A synthetic pass is necessary but is not a production privacy guarantee.

## Step 6 — Architecture decision checkpoint

Purpose: prevent an experiment from silently becoming production architecture.

Record one decision:

- **Proceed:** cross-user benefit is real and the boundary is plausible. Implement the canonical
  contracts in Step 7.
- **Revise:** signal exists, but attribution, concept alignment, normalization, decay, or
  contributor independence needs another isolated iteration.
- **Reject:** the shared relationship signal does not produce useful transfer or cannot be made
  acceptably safe. Preserve the personal graph and processor integrations without building a
  collective service.

The decision record must include experiment version, commit, fixtures, policies, metrics,
failures, limitations, and artifact paths.

## Step 7 — Repair canonical runtime contracts

Only after a `Proceed` decision:

- [ ] Define scope independently from visibility and contribution policy.
- [ ] Define immutable payload reference and handler contracts.
- [ ] Define the global concept registry plus private concept overlays and aliases.
- [ ] Define observation-event, support-aggregate, and serving-snapshot schemas separately.
- [ ] Version policy, processor, model, prompt, and projection provenance.
- [ ] Add owner deletion/erasure semantics without rewriting shared aggregate history.
- [ ] Add migrations and adapter contract tests for memory, SQLite, and PostgreSQL.
- [ ] Replace raw relationship multiplication and the learned-weight ceiling only through a
  versioned shadow profile with rollback.

Exit gate: private evidence cannot cross scope through any normal runtime path, and all learned
serving state can be rebuilt from policy-compliant events.

## Step 8 — Validate with real models and realistic data

- [ ] Repeat the transfer experiment with a stronger teacher and a smaller consumer model.
- [ ] Rerun real-model isolated gates and pairwise Mem0/Temporal integrations.
- [ ] Run a fixed development slice of LongMemEval without changing it during iteration.
- [ ] Add WikiConv for evolving conversation state and conflict/supersession behavior.
- [ ] Add EverMemBench only after bounded ingestion and evaluation costs are measured.
- [ ] Measure task success, recall, context tokens, latency, cost, false positives, unrelated
  regression, and small-model uplift.

Exit gate: the gain survives nondeterministic models and data outside the synthetic fixture.

## Steps 9–11 — Scale only proven behavior

Step 9 adds PostgreSQL observation events, aggregate projections, versioned snapshots, canary
promotion, rollback, audit, and partitioning. It does not introduce Neo4j, MongoDB, Pinecone, or
a second canonical store without measured necessity.

Step 10 adds a leased PostgreSQL job queue and a small Mac worker only after canonical storage is
hosted and reachable while the laptop is off. The Mac runs inference/enrichment; it does not
become the only copy of user data. Deployment must state how the worker starts, stays running,
receives secrets, connects over TLS, logs failures, retries work, and updates safely.

Step 11 adds procedures as a separate ordered structure after associative retrieval and learning
are validated. Procedures reference source atoms and outcomes but do not replace atom-to-atom
relationships. The gate is improved small-model task completion versus the same token budget of
unstructured memories.

## Deliberately not building yet

- hosted global accounts, public APIs, or a global production database;
- automatic promotion from an individual outcome to global serving state;
- secure aggregation or differential privacy implementation before the export boundary is
  characterized;
- distributed graph infrastructure;
- autonomous Mac execution before always-available canonical storage exists;
- GroupTags, recursive traversal, adaptive ranking, or procedures mixed into the first transfer
  experiment;
- training on hidden chain-of-thought.

These are not rejected capabilities. They are sequenced behind evidence so failures can be
attributed to one mechanism at a time.

## Progress log

Append one short entry after every work session.

| Date | Step | Change | Evidence/result | Next action |
|---|---|---|---|---|
| 2026-09-02 | 0 | Consolidated architecture into an executable roadmap | Relevant documents reconciled; `git diff --check` passed | Commit the documentation baseline |
| 2026-09-02 | 1 | Reproduced the pre-experiment baseline | 97 tests and 7/7 gates passed; maintained surfaces lint-clean | Build shadow core |
| 2026-09-02 | 2–3 | Added isolated collective core and transfer harness | 18/18 experiment checks and 104/104 unit tests passed; 2 live PostgreSQL tests skipped | Expand policy/adversarial fixture matrix |
| 2026-09-02 | 4 | Added selective cheap/expensive AI review cascade | 17/17 cascade checks and 110/110 unit tests passed; fixed-review quality matched with 3/13 escalations and about 24% relative cost | Test ambiguous cases and real reviewers |
| 2026-09-02 | 4 | Added deterministic ledger/vector/Mem0/Temporal feature extractor | 12 extractor contracts and 122/122 unit tests passed; cold, mature, ambiguity, conflict, supersession, degradation, lineage, and privacy behavior verified | Add real repository evidence adapters |
| 2026-09-03 | 4 | Connected the read-only repository feature adapter | 126 tests passed (2 live PostgreSQL tests skipped); one Mem0 smoke relation was observed without model calls or feature-path mutations and correctly escalated because attribution/vectors were unavailable | Observe an enriched public development slice and calibrate thresholds |
| 2026-09-03 | Pre-8 | Added and ran the isolated Mem0 entity/provenance quality gate | e2b achieved 100% conditional evidence precision/recall but only 71.4%/83.3% endpoint precision/recall; local 12B fallback failed from Mac memory/compute limits | Keep e2b proposal-only and evaluate a selective API or constrained-review fallback |
| 2026-09-03 | Pre-8 | Added guarded Mem0/vector cold-start lifecycle and comparison | New Mem0 edges start inactive; vector gating reduced wrong active weight 89.5%, but typed-edge precision/recall reached only 72.7%/66.7% and failed promotion | Add semantic validation for high-similarity proposals, then run retrieval comparison |
| 2026-09-03 | Pre-8 | Ran the new entity graph through five attributable-use rounds | Cold entity support raised hybrid MRR 0.413→0.533; multi-evidence feedback raised it to 0.575 and graph recall 0.583→0.708, but 30 uses caused 1,445 transitions across 289 edges | Test path-attributed bounded learning and held-out queries; separately validate held Mem0 edges semantically |

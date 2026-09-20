# Execution Roadmap

Status: active

Last updated: 2026-09-20

Active research next step: development-only query-sensitive routing/fusion experiment (step C in
[the learning/retrieval math audit](LEARNING-RETRIEVAL-MATH-AUDIT.md)). The local LoCoMo pilot
and development strength sweep are complete; no new learning parameters were promoted.
This track does not supersede the separate delivery step or un-defer collective production work.
Step B tracing is complete: the two saved cases reproduce exactly; the dance answer ranks 29
before packing despite receiving graph support. See audit section 7 before choosing a repair.
The first development-only fusion candidate is complete and not promoted: see
[FUSION-DIAGNOSTIC.md](FUSION-DIAGNOSTIC.md). Agreement gating traded ranking gains for losses
without improving average recall. Next isolate broad/specific and context-route activation;
do not continue an unconstrained fusion parameter sweep.
Semantic tag-prior initialization is now implemented as an opt-in calibration service; the
fixed development run created 625 bounded priors without improving retrieval. See
[TAG-SIMILARITY-PRIORS.md](TAG-SIMILARITY-PRIORS.md). It is not enabled on the live corpus.
The authorized 1x/3x/10x similarity-prior strength follow-up is also complete: no recall gain,
one rank regression at 10x. End strength-only sweeps; next isolate route selectivity and the
score transformation with controlled examples. The report includes manually reviewed traces.

Current delivery step: connector platform hardening — integrate the source-owned connector boundary

Connector delivery state: C0 through C4 passed; bundled C5/C6 connectors were superseded by the
source-owned packaging decision in ADR-0021; C7 remains deferred

Collective research step: 4 — deferred until a larger, more representative dataset exists

This is the operational source of truth for what we build next. The architecture documents
describe what the system may become; this file records the order in which we will prove and
implement it. Update the status, evidence, and decision log whenever a step changes state.

## Working rule

We validate the riskiest claim with the smallest faithful implementation before building
production infrastructure around it. The isolated deterministic `collective-transfer-v1`
experiment follows that rule: it does not mutate normal retrieval behavior, existing databases,
or global serving state.

That experiment has now proved the deterministic mechanism, but the next correlation-policy
comparison needs more representative data than the current fixture. The collective research
track is therefore deliberately deferred, not rejected. A separate local-product track can make
the already proven ingestion and retrieval foundations usable without committing to the global
graph architecture.

Status values are `not started`, `in progress`, `blocked`, `passed`, `failed`, `deferred`, and
`superseded`.
A step is `passed` only when its evidence is checked into the repository or linked below.

## Milestone map

| Step | Status | Outcome | Unlocks |
|---|---|---|---|
| 0. Preserve baseline | passed | Accepted scope and current behavior are recoverable | Safe experimentation |
| 1. Reproduce current baseline | passed | Existing tests and capability gates have recorded results | Trustworthy before/after comparison |
| 2. Build shadow collective core | passed | Policy math runs without production mutation | Cross-user experiments |
| 3. Prove positive and negative transfer | passed | A changes B through shared concepts only | Central thesis evaluation |
| 4. Compare weight policies | deferred | A measured policy wins over controls | Candidate learning policy |
| 5. Test privacy, poisoning, and lifecycle | deferred | Leakage and manipulation stay within declared gates | Safe schema design |
| 6. Make the architecture decision | not started | Proceed, revise, or reject is recorded from evidence | Production implementation |
| 7. Repair canonical contracts | not started | Scope, visibility, payload, and contribution are explicit | Real multi-scope runtime |
| 8. Validate with real models and data | not started | Gains survive realistic noise and model differences | Product evidence |
| 9. Add production collective storage | not started | Events, projections, snapshots, and rollback are durable | Hosted collective service |
| 10. Add an autonomous inference worker | not started | Remote jobs continue while the local client is offline | Continuous enrichment/evaluation |
| 11. Add procedures | deferred | Validated ordered guidance improves small models | Small-model enhancement layer |

## Local product track

This track packages only existing contracts. It must not promote experimental collective math or
change the privacy boundary.

| Step | Status | Outcome |
|---|---|---|
| D1. Checkpointed document pipeline | passed | One command runs canonical ingestion, configured processors, and a ledger audit with restart evidence |
| D2. Local application boundary | passed | Retrieval, explanations, candidate review, and attributable feedback are usable without raw CLI choreography |
| D3a. Recoverable local PostgreSQL | passed | Both legacy corpus and current-schema backups restored successfully; live adapter and API checks passed |
| D3b. Always-reachable canonical storage | deferred | A later trusted/hosted database is reachable without the local client |
| D4. Autonomous inference worker | deferred | Leased jobs continue after the local client disconnects |

D1 is documented in `LOCAL-MVP-RUNBOOK.md`; D2 and its loopback-only boundary are documented in
`LOCAL-API.md`. SQLite is explicitly the small/medium local mode, while PostgreSQL remains the
bounded large-ingestion mode. ADR-0018 selects laptop-only operation for now. D3a must pass before
large canonical ingestion; D3b and its security boundary must pass before D4 resumes.

D3a evidence and backup identities are in `LAPTOP-POSTGRES.md`. The application's automatic
tag-candidate and weight-ledger migration passed on an isolated restore of the historical corpus,
including provenance accounting, retrieval-input fingerprints, representative lexical results,
and current-version no-op startup. The canonical PostgreSQL corpus still uses its historical
schema and was not opened during the rehearsal. Its eventual upgrade remains a separate,
backup-first operational action. The backup verifier deliberately restores and inspects without
triggering application migrations.

## Connector product track

This track turns the local application boundary into a reusable connector and agent contract. It
does not make the loopback API public or un-defer hosted storage. ADR-0020 and
`CONNECTOR-API-PLAN.md` are authoritative for its boundary and acceptance matrix.

| Step | Status | Outcome |
|---|---|---|
| C0. Freeze connector boundary | passed | Public concepts, four capability profiles, transport ownership, security boundary, and ordered plan are reviewed and committed |
| C1. Contract fixtures | passed | DevUI-like structured and Slack-like mutable data validate against transport-independent request/response models |
| C2. External record lifecycle | passed | Source identity, versions, relations, sync runs, cursors, and tombstones are replay-safe across repositories |
| C3. Python SDK | passed | A connector performs sync, retrieval, and outcomes without Cortex-internal knowledge |
| C4. Local MCP adapter | passed | IDE agents use read/outcome tools with REST-equivalent policy and results |
| C5. Bundled DevUI connector | superseded | Structured conformance remains in Cortex; operational mapping belongs in its source-owned repository |
| C6. Bundled Slack connector | superseded | Mutable conformance remains in Cortex; operational mapping belongs in a separate connector repository |
| C7. Hosted remote REST/MCP | deferred | Authenticated remote agents connect only after D3b and the full authorization/operations gate |

C1 is committed and passed. C2's prerequisite is also passed: ADR-0019's shared migration registry,
SQLite `PRAGMA user_version`, PostgreSQL `schema_metadata`, future-version rejection, schema
invariants, transactional recovery tests, and live restored-corpus rehearsal are complete. C7
remains deferred until identity, scope authorization, TLS, rate limiting, audit,
retention/deletion, backup/recovery, and incident behavior are accepted and tested.

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

Step 10 adds a leased PostgreSQL job queue and a small inference worker only after canonical
storage is hosted and independently reachable. The worker runs inference/enrichment; it does not
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
| 2026-09-03 | Pre-8 | Compared the all-pairs learner with an atom-tag + `CO_USED` policy | Every per-round ranking, recall, and context-quality metric was identical while transitions fell 88.2%, from 1,445 across 289 edges to 170 across 34; all audits passed | Isolate atom-tag versus `CO_USED` contribution, then validate the winner on held-out and negative cases |
| 2026-09-04 | Pre-8 | Isolated feedback channels and added six never-rewarded paraphrases plus negative/collateral checks | Query↔selected-evidence learning matched all-pairs final training and held-out quality with 48.8% fewer transitions; atom tags improved rank, CO_USED improved graph recall but caused a hybrid recall loss; negative reversal and ledger audit passed | Budget query-evidence influence and eliminate transient held-out regressions on a larger query set |
| 2026-09-04 | D1 | Added one checkpointed document workflow over canonical ingestion, Tags, Temporal, Mem0, embeddings, and weight audit | 161 tests passed, 2 live PostgreSQL tests skipped, 2 subtests passed; `src` and `tests` passed Ruff; SQLite/PostgreSQL ingestion modes are explicit | Build the narrow local retrieval/feedback application boundary |
| 2026-09-04 | D2 | Added an optional loopback FastAPI transport over canonical ingestion, explainable retrieval, candidate review, and attributable feedback | 165 tests passed, 2 live PostgreSQL tests skipped, 2 subtests passed; API flow and fixed loopback bind are covered; a real Uvicorn health/OpenAPI smoke passed; `src` and `tests` passed Ruff | Choose hosted PostgreSQL and define backup/restore acceptance gates |
| 2026-09-04 | D3a | Selected laptop-only storage; changed Ollama defaults to laptop loopback; added atomic PostgreSQL backups, checksum manifests, and isolated restore verification | 172 tests passed, 2 live PostgreSQL tests skipped, 2 subtests passed; lint and diff checks passed; a real local tag/embedding/retrieval/API smoke passed; live Docker restore is blocked by a stale optional Model Runner socket before PostgreSQL startup | Repair Docker Desktop locally, run live PostgreSQL tests, then create and verify the first real backup |
| 2026-09-05 | D3a | Completed live storage acceptance after Docker restart; made restore verification inspect legacy schemas and require pgvector/core tables | 176 tests passed with PostgreSQL enabled; original 193 MiB archive restored with all ten table counts matching, including 272,209 atoms; current-schema fixture restored with weight events; real PostgreSQL API health passed; temporary databases cleaned up | Rehearse corpus migration and compare retrieval on a restored copy before upgrading canonical data |
| 2026-09-18 | C0 | Documented the external connector and agent boundary plus its phased API/SDK/MCP delivery plan | ADR-0020, connector plan, architecture, context ledger, README, and roadmap agree; local links and `git diff --check` passed | Review and commit C0, then build transport-independent DevUI-like and Slack-like contract fixtures |
| 2026-09-18 | C1 | Added transport-independent source, record, relation, sync, scope, query, evidence, context, and outcome contracts plus strict mapping codecs and DevUI/Slack fixtures | 210 tests passed, 2 optional PostgreSQL tests skipped, 5 subtests passed; maintained Ruff and Pyright surfaces passed | Review and commit C1, then implement ADR-0019 migrations before C2 persistence |
| 2026-09-19 | ADR-0019 | Added a shared ordered migration registry, transactional adapter-owned baseline migrations, durable schema versions, invariant-before-version checks, future-version rejection, and automatic pre-upgrade SQLite backups | 219 tests passed, 3 optional PostgreSQL tests skipped, 5 subtests passed; Ruff, Pyright, and diff checks passed; live PostgreSQL execution unavailable without a test DSN or Docker server | Rehearse the version-0 PostgreSQL upgrade on a verified restored copy, then mark the migration gate passed and begin C2 |
| 2026-09-19 | ADR-0019 | Rehearsed the version-0 PostgreSQL migration on an isolated restore of the 193 MiB legacy archive | Schema version reached 1; 26,300 documents and 272,209 atoms were preserved; 62,883 unreviewed tag edges were quarantined, 19 explicit edges retained, and 57,914 weight baselines created; 36/36 lexical comparisons plus atom/embedding fingerprints matched; no-op reopen and all 222 tests with live PostgreSQL passed; temporary databases removed and canonical corpus untouched | Begin C2 external record lifecycle persistence on a feature branch |
| 2026-09-19 | C2 | Added replay-safe source registration, record versions and predecessor lineage, source relations, tombstones, sync batches, repairable item failures, and cursor commits with memory, SQLite, and PostgreSQL parity | 249 tests and 5 subtests passed with live PostgreSQL against an isolated database; maintained Ruff and Pyright surfaces passed; canonical corpus untouched | Review and merge C2, then design the thin Python SDK and REST mapping for C3 |
| 2026-09-19 | C3 | Added loopback source-sync REST routes, generated OpenAPI contract shapes, the typed `cortex` client/session helper, structured retry guidance, and a repository-free connector contract-test kit | 255 tests and 5 subtests passed with live PostgreSQL against an isolated database; maintained Ruff and expanded Pyright surfaces passed; C3 remains open for retrieval/outcome projection | Decide generic external-record projection and scope routing before adding SDK query and outcome methods |
| 2026-09-20 | C3 | Added deterministic scope-to-retrieval projection, version lineage, tombstone serving suppression, projection-gated cursor commits, opaque evidence context packs, and attributable outcome reporting through REST and the Python SDK | All 264 collected tests passed on maintained local surfaces with 11 optional integration skips; 44 targeted tests passed with live PostgreSQL in an isolated database; Ruff and Pyright passed; temporary database removed and canonical corpus untouched | Begin C4 local MCP adapter design; keep hosted C7 deferred |
| 2026-09-20 | C4 | Added a local stdio MCP adapter with scoped search, bounded context, retrieval-bound evidence hydration, caller-safe explanation, and attributable outcome tools over the shared access service | All 267 collected tests passed with live PostgreSQL in an isolated database; REST/MCP fixture parity, tool annotations, hidden atom identity, outcome replay, and a real stdio subprocess passed; Ruff, Pyright, and diff checks passed; temporary database removed and canonical corpus untouched | Begin C5 DevUI reference connector; keep remote Streamable HTTP MCP deferred to C7 |
| 2026-09-20 | C5 | Added a public-SDK-only DevUI reference connector with strict snapshot decoding, file/function/module/proposal mapping, deterministic chunking, exact-version relations, cursor-safe orchestration, CLI sync, and evidence-to-navigation pointers | All 274 collected tests passed with live PostgreSQL in an isolated database; SDK-to-REST round trips mapped retrieved functions back to DevUI file/line identity on SQLite and PostgreSQL; rejection withheld cursor commit; Ruff, Pyright, and diff checks passed; temporary database removed and canonical corpus untouched | Begin C6 Slack reference connector; compare real repetition before extracting generic connector scaffolding |
| 2026-09-20 | C6 | Added a public-SDK-only Slack connector with strict event-page decoding, channel/project allowlists, incremental cursors, edit lineage, exact-version thread replies, deletion tombstones, CLI sync, message pointers, and the shared `sync_source_batches` SDK primitive | All 281 collected tests passed with live PostgreSQL in an isolated database; exact replay, current edit retrieval, deletion suppression, navigation identity, outcomes, and SQLite/PostgreSQL REST round trips passed; Ruff, Pyright, and diff checks passed; temporary database removed and canonical corpus untouched | Local connector plan is complete; keep C7 hosted remote integration deferred until its identity, authorization, and operations gates are explicitly accepted |
| 2026-09-20 | ADR-0021 | Corrected connector packaging ownership: removed DevUI/Slack runtime packages, CLIs, extras, and source-export models from Cortex while retaining generic fixtures, contract tests, and `sync_source_batches` | All 269 collected tests passed locally and with live PostgreSQL in an isolated database; removed commands were absent after reinstall; Ruff, Pyright, and diff checks passed; temporary database removed and canonical corpus untouched | Treat C7 as a separate hosted-security decision; operational connectors belong in source-owned repositories |

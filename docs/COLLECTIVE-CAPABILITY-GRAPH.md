# Collective capability graph

- Status: Authoritative future-scope specification
- Date: 2026-09-02
- Decisions: ADR-0013 and ADR-0014

## Thesis

Data Retrieval is not only a personal memory system. Its long-term product is a
privacy-preserving, model-agnostic collective capability substrate.

Private evidence teaches a shared semantic routing graph through attributable outcomes. The
shared graph distributes learned routing and, later, validated procedures without granting one
user access to another user's source atoms. Better models can act as better processors and
teachers; weaker or newer models can consume the durable capability accumulated before they
were connected.

The durable principle is:

> Evidence accumulates, interpretations evolve, and learned attention is perishable.

## What is shared

The system is one **logical, layered graph**, not one flat database containing every user's
payload:

```text
                         global capability graph
              shared concepts, aliases, associations,
              public evidence, validated procedures
                                  ^
                    controlled aggregation/promotion
                                  ^
             organization, domain, and cohort overlays
                                  ^
                     privacy-safe observations
                                  ^
                project and personal evidence overlays
             private atoms, interactions, and preferences
```

The global graph primarily learns **where useful evidence is likely to be**, not the private
content that caused a relationship to become useful.

Potentially global:

- stable accepted concept identities and public aliases;
- evidence-backed concept hierarchy;
- aggregated tag-to-tag usefulness and inhibition;
- query-intent-to-concept routing patterns;
- versioned retrieval-policy improvements;
- public evidence with normal provenance;
- sufficiently generalized and validated procedures.

Private by default:

- atom payloads, documents, chats, and private source references;
- atom-to-concept attachments;
- raw queries, responses, retrieval paths, and interaction history;
- personal and project weights or preferences;
- user, document, atom, and session identifiers.

## Scope and identity contract

`namespace` is an operational collection boundary; it is not by itself a privacy or security
model. The collective graph requires independent fields for:

- `concept_id`: stable semantic identity reusable across scopes;
- `scope_type`: global, organization/domain, project, user, or device/private;
- `scope_id`: owner of a non-global overlay;
- `visibility`: private, organization, public, or aggregated-only;
- `contribution_policy`: whether and how observations may leave the scope.

A private tag proposal may be accepted locally without becoming a global concept. Global
promotion is a separate evidence, privacy, abuse-resistance, and governance decision.

Retrieval composes permitted layers under a versioned profile. Global evidence is a prior;
project and personal context can strengthen, suppress, or contextualize it. Raw magnitudes are
never compared directly across scopes with different traffic volumes.

## Collective learning boundary

An individual outcome never mutates a global serving edge directly. The learning path is:

```text
private retrieval and task outcome
    -> attributable local observation
    -> consent, sensitivity, and contribution-policy check
    -> identifier and metadata minimization
    -> bounded contribution
    -> independent-contributor aggregation
    -> shadow global snapshot
    -> held-out quality, privacy, and abuse gates
    -> canary/promotion or rejection
```

A shareable observation may contain only the minimum required information:

- globally accepted concept IDs;
- behavioral relation type;
- bounded outcome category and contribution;
- coarse, approved context profile;
- policy version and coarse time bucket.

It must not expose source payload, raw query, atom/document/session identifiers, or a public user
identifier. Clear-text concept pairs can themselves be sensitive. Rare or sensitive pairs
therefore require suppression, minimum contributor thresholds, secure aggregation, or a
stronger privacy mechanism before leaving a private scope.

Global aggregation must account for independent contributors, not raw event volume. One user,
automation loop, or coordinated identity cluster must not be able to dominate an edge by
repetition.

## Selective AI review cascade

Ordinary observations use deterministic validation, contributor bounds, decay, and aggregation.
Uncertain, high-impact, conflicting, novel, rare, sensitive, concentrated, or regression-causing
entries may be escalated to a replaceable AI reviewer. The cheap gate uses bounded metadata and
aggregate features; it does not require private payloads merely to decide whether review is
worthwhile.

Expensive review is asynchronous. Until it finishes, the active serving snapshot remains
unchanged. High-risk updates are held rather than provisionally applied. Review queues have
explicit budgets and prioritize the product of uncertainty, expected impact, and risk, with hard
safety triggers for sensitive data and candidate regressions.

An AI reviewer never writes an arbitrary raw weight. It returns a structured, versioned verdict:
support, oppose, uncertain, or abstain; bounded confidence and recommended strength; reason
codes; model family/profile; time; and an evidence digest. Hidden reasoning is neither requested
nor stored. Uncertain and abstaining reviews add no support.

Behavioral support and AI-review support remain distinct aggregation channels. A review policy
converts model confidence, proposed strength, and measured reviewer reliability into capped
equivalent support. Repeated reviews from one model family/version are not independent evidence.
Multiple models from the same correlated family must not masquerade as independent reviewers.
Review evidence may strongly influence a relationship but remains subject to shadow evaluation,
canary promotion, decay, inhibition, audit, and rollback.

The initial deterministic cascade result and its limitations are recorded in
`REVIEW-CASCADE-EXPERIMENT.md`.

## Owner control and erasure

Source evidence is immutable to inference, learning, and refinement. That rule does not remove
the owner's ability to tombstone, export, apply retention limits to, or securely erase private
data. Administrative privacy actions are separately authorized and audited.

Collective contribution policies must define revocation and unlearning behavior before accepting
real user observations. Exact individual removal may conflict with secure aggregation or
differential privacy, so the product must state clearly whether a contribution is revocable,
aggregated irreversibly after a threshold, or excluded from sharing entirely. “Append-only” is a
learning integrity rule, not a justification for permanent possession of user data.

## Three distinct learning records

Collective learning separates:

1. **Observation events** — immutable records that a bounded positive, negative, conflict, or
   uncertain outcome occurred under a named policy.
2. **Support aggregates** — rebuildable positive and negative evidence accumulated for one
   scoped relationship.
3. **Serving snapshots** — versioned, bounded views used by retrieval after decay,
   normalization, privacy thresholds, and quality gates.

The current before/after weight-event ledger is the local correctness substrate. Global
learning will extend it with observation semantics and support aggregates; it must not discard
or rewrite existing history.

## Weight domain

Raw learned support is non-negative and unbounded:

```text
positive_support in [0, infinity)
negative_support in [0, infinity)
```

This applies to accumulated support for:

- atom-to-concept affinity (`HAS_TAG` equivalent);
- concept-to-concept behavioral association (`RELATED_TO` equivalent);
- group membership (`CONTAINS` equivalent);
- future procedure usefulness under explicit conditions.

Confidence, reliability, probability, cohesion, privacy risk, and final retrieval influence
remain bounded. A high raw support value represents accumulated evidence volume or maturity; it
is not a probability and does not receive direct serving influence.

Signed negative raw weights are not the default. Negative support, contradiction, factual
conflict, and behavioral inhibition are preserved explicitly rather than collapsed into one
ambiguous signed scalar.

## Passive decay and active negative learning

Only learned behavioral attention decays. Provenance, source identity, factual lineage,
adjacency, immutable observations, and historical truth are never weakened merely by time.

Passive decay is lazy: the system does not scan and rewrite the whole graph on a timer. A
versioned policy evaluates or materializes current support when an edge is read, updated, or
included in an active maintenance partition.

The first proportional-decay candidate is:

```text
D(x, elapsed, half_life) = x * 2^(-elapsed / half_life)

P_now = D(positive_support, positive_elapsed, positive_half_life)
N_now = D(negative_support, negative_elapsed, negative_half_life)
R_now = max(0, P_now - negative_penalty * N_now)
```

The half-lives and negative penalty are policy parameters, not domain constants. Maturity,
relation type, scope, and independently verified support may select different profiles only
after an ablation.

Verified bad outcomes add bounded negative observations immediately. An item being returned,
ignored, or merely co-occurring is not a negative outcome. Attribution must identify evidence
actually used and distinguish evidence failure from model, tool, intent, or environment
failure.

Local negative learning may take effect immediately. A single private contributor supplies
only a bounded global observation; global inhibition requires aggregation and abuse controls.

## Relative serving influence

Competing unbounded raw supports are converted to bounded influence inside a meaningful
comparison neighborhood:

```text
relative(R, reference) =
    clamp(log1p(R) / log1p(max(1, reference)), 0, 1)
```

The historical baseline uses the maximum support in the comparison set. A robust percentile
reference is an accepted experiment for outlier resistance. The comparison set must be explicit
and normally consists of one relation type within one relevant query neighborhood, source
concept, group, and scope. It is never the arbitrary maximum across the whole global graph.

Examples of correct comparison boundaries:

- atom affinities competing under the same concept;
- outgoing relations of the same type from one concept;
- member contributions inside one group;
- candidate paths competing for one query;
- global, organization, project, and personal layers normalized separately before blending.

Multi-hop synergy uses normalized edge influence, relation-aware budgets, visited sets, and a
path-length penalty. No traversal formula may multiply arbitrary unbounded raw weights.

## Lifecycle

Serving state may classify learned relationships as:

- **hot** — recent, repeated, independently verified usefulness;
- **warm** — established but less recently reinforced;
- **cold** — weak or stale and eligible only for exploration/fallback;
- **dormant** — absent from normal traversal but recoverable from history or new evidence;
- **inhibited** — explicit negative evidence discourages traversal under named conditions.

Dormancy removes an edge from active serving indexes; it does not delete its observations.
Renewed verified evidence may reactivate it. Snapshot promotion and rollback are versioned, but
the graph is not copied or manually versioned edge by edge.

## Context and disagreement

A global graph cannot turn contextual usefulness into one universal truth. Observations and
aggregates may be conditioned by domain, environment, jurisdiction, software/model version,
temporal validity, or other approved coarse context. Conflicting evidence is retained rather
than averaged into a misleading factual claim.

Behavioral association is not causality, factual support, or procedural order. Procedures are
a separate structure containing ordered steps, prerequisites, branches, tools, checks,
expected outcomes, failures, counterexamples, validity conditions, and source lineage.

## Model relationship

Models are replaceable teachers and consumers:

- stronger models may propose better tags, facts, conflicts, groups, and procedures;
- real outcomes validate or reject those proposals;
- accepted capability is stored in model-independent concepts, observations, and procedures;
- weaker or future models consume the accumulated graph through adapters;
- a provider/profile change creates a versioned projection and never overwrites source truth.

Every processor and namespace/scope has an explicit projection lifecycle:

- **active** outputs are eligible for ordinary serving;
- **shadow** outputs are evaluated but cannot affect normal answers or learning;
- **superseded** outputs remain auditable but do not crowd retrieval;
- **rejected** or invalid outputs retain the minimum lineage required by policy and are
  ineligible for serving.

A newer model version therefore adds a candidate interpretation; it does not make every old and
new interpretation compete simultaneously.

Models do not train the global graph by confidence alone. Synthetic outputs cannot validate
other synthetic outputs without independent evidence or an objective outcome.

## Physical scale

One logical graph does not require one physical server forever. Stable IDs, scoped observations,
partitionable event history, bounded queries, asynchronous aggregation, and rebuildable
projections must allow later partitioning without changing domain semantics.

PostgreSQL is the initial canonical online target. Partitioned or distributed PostgreSQL,
archived event partitions, dedicated rebuildable vector projections, or another measured
projection may be introduced after capacity evidence. The domain must not require a transaction
across the entire global graph.

## Historical mathematics preserved as baselines

Recovered source authorities in the DevUI repository are:

- `subprojects/data-memory/research/core-tag-functionality/extracted-txt/Weight Updating
  Mechanisms.txt`;
- `subprojects/data-memory/research/core-tag-functionality/extracted-txt/Information
  Retrieval.txt`;
- historical revision `af70db2:cortex/core/gravity.py`;
- `subprojects/data-memory/PLAN2.md`;
- Cortex ADR `0003-tag-core-and-catalog-canonicalization.md`;
- Cortex `LEARNING_MATH_SPEC.md`, `GRAVITY_SCORING_SPEC.md`, and
  `TAG_RELATION_AND_GROUP_SPEC.md`.

The first Tags document used bounded discrete updates:

```text
initial weight = 0.5
verified useful use: W' = min(1, W + 0.05)
unused after N interaction ticks: W' = max(0, W - 0.02)
reset used counters after each decay epoch
```

The same revision proposed local relevance-drop traversal:

```text
sample_size = 0.00025 * requested^2 + 0.05 * requested
decline_i = (W_old - W_new) / max(epsilon, W_old)
average_decline = sum(decline_i) / (n - 1)
```

The original text wrote the decline with the opposite sign while sorting descending; the
corrected positive-decline form above is the executable baseline.

The first DevUI gravity implementation used:

```text
never-accessed velocity = 1 / log(age_seconds + 2)
adoption velocity = 1 / log(first_access_delta_seconds + 2)
mass = log(1 + access_count)
gravity = 1.0 * velocity + 2.0 * mass + 1.5 * centrality
```

Its keyword-found/vector-missed self-correction added `+0.5` gravity without an outcome. That
specific update is rejected because retrieval disagreement is calibration evidence, not proof
of usefulness.

Later Cortex active-feedback baselines were:

```text
selected            +0.04
explicit_positive   +0.06
outcome_win         +0.10
skipped             -0.05
explicit_negative   -0.08
outcome_loss        -0.12
uncertain_conflict   0.00 until confirmed

effective_delta = base_delta * multiplier * namespace_baseline
```

Cortex also used a 30-day default half-life for an atom recency modifier:

```text
recency = 0.90 + 0.20 * 2^(-age_days / half_life_days)
```

That formula changed retrieval recency; it was not passive relationship decay.

The later Cortex decision replaced bounded raw relation weights with non-negative unbounded
support and relative `log1p` normalization. It did not finish adapting passive decay, negative
learning, group membership, and every retrieval path to that domain. Both historical revisions
remain experiment baselines; neither set of constants is accepted without evaluation.

## Decisive validation

The central thesis is validated only if cross-user capability transfers without private payload
transfer:

1. Give A and B disjoint private atoms mapped to shared global concepts.
2. Measure B on held-out retrieval and tasks.
3. Generate attributable successes and failures using only A.
4. Export only policy-compliant relationship observations.
5. Build a candidate global snapshot and measure B again.
6. Probe whether A's content, identity, rare interests, or source structure can be inferred.
7. Repeat with a strong teacher and a smaller consumer model.
8. Add stale, noisy, and malicious contributors and test decay, independence limits, and
   rollback.

Required metrics include task success, evidence recall, context tokens, false positives,
unrelated-task regression, independent-user contribution distribution, privacy leakage,
poisoning resistance, decay/reactivation behavior, and small-model uplift.

Integration smoke tests may run early, but no global promotion or replacement conclusion is
allowed until the relevant isolated and cross-user gates pass.

## Known implementation mismatches

The current runtime is not the collective system described here. Known mismatches include:

- namespace isolation without independent scope/visibility/contribution fields;
- namespace-specific tags rather than a stable global concept registry with overlays;
- a default learned-weight ceiling of `10` in `LearningService`;
- raw relationship multiplication in some expansion paths;
- incomplete relative normalization for atom-tag, tag-tag, and group-member comparisons;
- no passive relationship decay, maturity, dormancy, or reactivation policy;
- no separate positive/negative support aggregates;
- no privacy-safe contribution, independent-user aggregation, global snapshot, or
  anti-poisoning pipeline.

These are ordered future repairs, not reasons to rewrite the implemented canonical-evidence,
tag-lifecycle, temporal, processor, or immutable-event foundations.

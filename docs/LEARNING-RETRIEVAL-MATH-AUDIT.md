# Learning-to-retrieval mathematical audit

Date: 2026-09-06. Status: audit complete; repairs proposed, not implemented.

## Decision

Retain the atom-centered data model, canonical evidence, processor boundaries, and layered
collective vision. Do not promote the 3x/10x settings. The next work is a faithful, observable
local routing experiment, not a database change or a broad parameter search. Current results
evaluate a bounded local learner, not the full documented collective/maturity model.

This audit used local code, preserved historical mathematics in
[the collective design](COLLECTIVE-CAPABILITY-GRAPH.md), and saved benchmark outputs. It did
not re-run retrieval, invoke models, change canonical databases, or tune serving behavior.
Historical formulas below are from the repository's documented historical reconciliation;
the original remote repositories were not fetched afresh in this audit.

## 1. Quantities must have different meanings

| Quantity | Meaning | Must not be interpreted as |
|---|---|---|
| Processor confidence | Provider/attachment confidence | Independent outcome evidence |
| Positive and negative support | Attributable observations accumulated over time | Probability of relevance |
| Relative influence | Preference within a declared neighborhood | Absolute maturity |
| Query activation | Relevance of a starting point for this query | Global popularity |
| Route utility | Evidence that following a route helps a task | Co-occurrence alone |
| Ranking score | Ordering heuristic for this candidate pool | Calibrated success probability |

The current code has raw weights and confidence fields, but these do not implement all six
meanings separately. More support from repeated, correlated feedback is not necessarily more
independent evidence. No schema expansion is required merely to establish these contracts.

## 2. Implemented local equations

Sources: `src/data_retrieval/services/learning.py`, `services/retrieval.py`,
`storage/sqlite.py::search_tag_hits`. This describes the SQLite benchmark path.

Let s be +1/-1 for explicit outcome; m is normally 1 and is 2 when the existing Mem0-use
heuristic applies. Selected source credit c=1; derived source-lineage credit normally c=0.25.
The benchmark rewarded exact source atoms only, with m=1.

* Atom/tag: w' = clip(w + s * 0.05 * c * m, 0, 10), only for attached tags matching query tags.
* Existing co-use/tag relation: w' = clip(w + s * 0.10 * m, 0, 10).
* New positive relation starts at 0.10*m; negative feedback creates no missing relation.
  The creation branch does not call the clamp, unlike existing-edge updates. Defaults are safe;
  a future configurable large-step experiment needs validation here.
* Query-to-evidence policy joins accepted query tags to credited evidence tags. Pairs are
  sorted into undirected `co_occurs` edges. Up to 12 nodes are selected, with query tags first
  and ID sorting within groups. This is a computational bound, not relevance-based selection.
* Co-use joins selected/credited atoms. Relation updates do not apply the per-source credit
  factor used by atom-tag updates. Uniform relation credit is a separate policy choice.

Negative events remain in the ledger, but the serving aggregate floors at zero: there is no
separate negative-support or inhibition value in this learner. No passive behavioral decay
is applied here. The Mem0 multiplier is a heuristic, not calibrated reviewer reliability.

Tag raw score: T(a,q) = sum(confidence_at * log2(1+w_at)) / number_of_query_tags.
SQLite sums only canonical matching tags. Tag weights therefore have diminishing marginal
effect before cross-candidate normalization.

Each channel normalizes as N(x_a)=x_a/max_b(x_b), or empty if the maximum is non-positive.
Base weights are tag 0.45, lexical 0.25, semantic 0.30, redistributed over enabled/available
channels. Tag availability is based on query-tag presence, not demonstrated tag-channel quality.
No calibrated uncertainty or background tag-frequency term controls this fusion.

F(a,q)=min(1, alpha_T*N(T)+alpha_L*N(L)+alpha_V*N(V)+0.12*N(R)+temporal_score).
Role-aware evidence packing follows ranking. Temporal channels were disabled in this pilot.
The final clamp can produce ties; inspect occurrence before blaming it for measured losses.

### Relationship construction

The base seed set is the union of tag, lexical and semantic candidates. Expansion receives IDs,
not their base relevance scores.

* Query-tag relations contribute w*confidence to neighboring tags (parent-to-child factor .8,
  child-to-parent .5). Related-tag search first limits hits using attachment score, then multiplies
  each hit by the strongest matching related-tag strength. Thus multiple matches can affect the
  attachment sum, but distinct route strengths are not combined individually.
* Co-use adds w*confidence for each touching seed; adjacency adds .35*w*confidence. Weak and
  strong seed membership use the same edge rule. Multiple neighbors can accumulate support.
* Mem0 support/entity paths use products and max propagation with bounded entity/link pools;
  they likewise do not carry the original seed relevance through the path.
* These different channels are summed into R and normalized together over candidates.

Consequences to distinguish from established failures:

1. Uniform scaling of an entire raw channel is canceled exactly by max normalization.
   Scaling only learned deltas is NOT uniform scaling; our actual experiment did change scores.
2. Relatedness is not necessarily task relevance. Broad seed sets can activate much of a graph.
3. Structural adjacency, processor support and learned utility compete in the same denominator.
4. Candidate limits and graph degree can affect results independently of learning quality.
5. Seeing a route in a result does not establish that the route caused the successful outcome.

These are concrete mechanism properties, not proof that any single property caused the null
benchmark. Query-sensitive expansion is not entirely absent: query tags activate related tags.
The specific missing information is seed relevance in atom/entity expansion.

## 3. Historical intent versus shadow implementation

The preserved original Tags control starts at .5, adds .05 for useful use capped at 1, and
subtracts .02 after unused interaction epochs. Its relative-drop traversal requires a defined
ordering, sampling rule and stopping neighborhood; it is not the present one-pass expansion.
Cortex recorded outcome-specific increments (.04 selection, .06 positive, .10 win; negative
counterparts), and a recency modifier .90+.20*2^(-age/half_life). Recency of evidence is NOT
decay of learned relationships. Automatic punishment for a skipped item is not endorsed here.

The accepted later design instead allows non-negative unbounded accumulated support and bounded
relative influence. `collective/policy.py` already implements an isolated shadow projection:

    decayed_observation = support * 2^(-age_days / half_life_days)
    P, N = separately aggregated, contributor-capped positive/negative support
    E = max(0, P - negative_penalty*N)
    influence = clip(log1p(E) / log1p(max(1, neighborhood_reference)), 0, 1)

Behavioral and AI-review support are separate, with separate contributor/reviewer caps.
The reference is maximum or configured percentile of outgoing positive effective supports.
Projection state records cold/warm/hot/dormant/inhibited. The shadow ranker adds a bounded
best-route bonus to supplied private candidates; it does not itself discover missing candidates.

Important open math questions even in this shadow model:

* Max-relative log influence distinguishes preference, not statistical confidence. For a lone
  edge with E>=1, influence is 1 irrespective of much larger support.
* Decay before contributor capping means a contributor far above the cap can remain at the cap
  for a long time. This is not the same dynamics as decaying an already capped contribution.
* P-N preserves counterevidence in storage but equal effective support can hide very different
  conflict histories unless the consumer uses P and N separately.
* Percentile normalization is an alternative with its own clipping behavior, not an assumed fix.

The LoCoMo results cannot select between these shadow policies: none was the local learner used.

## 4. Saved experimental evidence

Runtime artifacts (ignored local data):
`data/results/locomo-learning-v1/scoring-20260905T201307Z/report.json` and
`data/results/locomo-learning-v1/strength-20260905T211014Z/report.json`.

The fixed evaluation pilot has 45 disjoint-evidence and 22 shared-evidence questions across
three histories. Disjoint means separate from feedback labels, not absent from ingestion.

| Configuration | Disjoint recall / MRR | Shared recall / MRR |
|---|---|---|
| Frozen hybrid | .561111 / .395855 | .690909 / .488943 |
| Atom-tag learner | .561111 / .395855 | .690909 / .490025 |
| Full local learner | .561111 / .395855 | .690909 / .490025 |
| Vector-only, lineage-aware | .700000 / .428025 | .587879 / .453030 |

Direct-only vector disjoint recall/MRR is .677778/.417901; the advantage remains. Full and
atom-only each consumed 66 feedback events. Full made 75 atom-tag, 6 atom-link and 1,014
tag-relation update operations (not distinct-edge counts). Mechanical checks passed.

Only conv-41-q94 improved first relevant rank, 7 to 6, under both learning policies; all other
questions had unchanged recall and first relevant rank. Relative to atom-only, full learning
changed relationship and final scores on 39 questions, including 25 disjoint questions, but
only two top-10 orders changed. Maximum absolute relationship/final deltas among matched returned
items were .063383/.007606. This does not measure unreturned candidate changes.

Actual development-only aggregate amplification preserved initial weights and scaled learned
deltas at 1x/3x/10x, with a 0..10 clamp on changed values. All 26 development queries were rerun
with cached features; 1x reproduced prior IDs. At 3x aggregate recall/MRR were unchanged. At 10x,
shared MRR .558442 -> .560606; disjoint MRR .167222 -> .165556; recall unchanged (.636364/.466667).
These disposable overrides are not ledger-consistent training histories and never belong in
production. This tested reinforcement strength, not graph maturity or fresh experience.

### Four inspection cases, not a representative new evaluation

Selected first two direct-recall wins and losses in report order; no further retrieval ran.

| Case | Query subject | Hybrid / vector direct recall | Saved trace |
|---|---|---|---|
| conv-30-q9 | City both Jean and John visited | 1 / .5 | Hybrid gold at ranks 1,3; rank-3 item has tag=1, semantic=.673417, relationship=.589660, final=.722784 |
| conv-30-q3 | What Jon and Gina share | .25 / 0 | Hybrid gold at rank 8: tag=0, lexical=.857143, semantic=.641112, relationship=.417072 |
| conv-30-q42 | Winning dance piece | 0 / 1 | Vector gold rank 3, normalized semantic=.948294; absent from hybrid top 10 |
| conv-41-q49 | Food dropped at shelter | 0 / .5 | Vector gold rank 5, normalized semantic=.875771; absent from hybrid top 10 |

For conv-30 all 447 atoms are semantic candidates and 369 source atoms receive relationship
scores in these cases. For conv-41-q49 the union is 725, semantic pool 500, relationship pool 663.
This substantiates broad activation, not a claim that every activated relation is irrelevant.
Shared semantic retrieval supplies the missing gold candidates to hybrid before ranking/packing;
saved top-10 traces do not tell us their full hybrid rank or whether packing excluded them.
The city case supports retaining hybrid capability; the dance case motivates tracing fusion and
packing before prescribing vector fallback. No attribution to a single channel is causal yet.

### Manual output-quality review

At the user's request, the assistant read the actual top-three hybrid source texts and all
official evidence texts for conv-30-q9 and conv-30-q42 directly from the prepared SQLite store
in read-only mode. This is a two-case qualitative inspection, not a new quality score, live
generation test, or representative sample. No final model answers were generated by this pilot.

* City question (q9): ranks 1 and 3 contain Gina saying she has been to Rome and Jon saying he
  took a recent trip to Rome. These genuinely support the common-city answer. Rank 2 discusses
  Paris, which is relevant travel context but is not the shared destination. Importantly, the
  question says "Jean and John" whereas the evidence speakers are Gina and Jon. The benchmark
  labels associate these texts, but this inspection cannot resolve that naming inconsistency.
  Report this as label-aligned retrieval success with a question/entity-quality caveat, not
  proof of correct identity resolution. Do not silently rewrite the frozen question.
* Dance question (q42): the top result concerns Jon's own crew winning a local competition,
  not Gina's team. Rank 2 is Gina mentioning a trophy but not the requested piece. Rank 3 is
  Gina describing her team's regional win at fifteen: good contextual evidence, but it still
  does not identify the dance. The labeled answer turn D1:19 explicitly names a contemporary
  piece, "Finding Freedom." Vector retrieval puts it at rank 3; hybrid omits it from the top 10.
  Thus the missed label corresponds to a substantive answer-bearing omission, not merely an
  arbitrary preference for one equivalent text. The short answer turn depends on conversation
  context, whereas distractors repeat dance/competition/winning language.

Implication for step B: include contextual, short answer turns and actor identity in the trace.
Inspect adjacency from the correct contextual turn, not just whether any adjacency score exists.
The dance case motivates testing query-weighted context-to-answer navigation; it does not yet
prove that adjacency rather than fusion/packing is the cause. Human review should accompany
future small development comparisons: question, top items, missed evidence, route and an explicit
useful/context-only/wrong-actor judgment. It need not involve an extra model call per item.

## 5. Mathematical tools: minimum justified additions

1. Query-conditioned bounded activation: pass seed scores into expansion. Test an explicit
   route form activation(seed,q)*influence(edge), with fixed budgets and no unbounded raw products.
   Compare sum versus max aggregation only if route duplication/degree diagnostics justify it.
2. Support/confidence separation: retain P,N and independent-evidence counts; consider shrinkage
   toward a prior for sparse routes. A Beta-style model is only appropriate once opportunities,
   successes, failures and dependence are defined; clicks and unselected items are not trials.
3. Background-frequency correction: consider smoothed lift or degree correction if common-tag
   overpromotion is observed. Raw PMI can overreward rare coincidences; do not install it blindly.
4. Contrastive credit: distinguish useful routes from plausible competing routes, with explicit
   outcomes or controlled ablations. Log exposure/selection before any propensity correction;
   retrieved-only positive feedback otherwise cannot teach recovery of never-exposed evidence.
5. Separate route utility from factual/entity relation confidence. Neither co-occurrence nor
   processor agreement is independent proof of usefulness or a shared global truth.

No need yet for graph neural networks, a new database, recursive unrestricted traversal, a large
learned ranker, or AI grading every interaction. Each tool above must earn its complexity.

## 6. Ordered repair plan and gates

| Step | Deliverable | Exit condition |
|---|---|---|
| A (this audit) | Equations, evidence, mismatches and preserved hypotheses | Complete |
| B (complete, 2026-09-06) | Opt-in route contributions and pre/post-pack ranks | Both saved cases reproduce returned atom IDs and scores; fresh retrieval-event IDs are expected |
| C | Isolated seed-relevance propagation policy | Controlled query changes route activation; irrelevant seeds cannot contribute equally; deterministic bounded work |
| D | Development comparison against unchanged and vector baselines | Report recall, rank, regressions, candidate recall and cost; no evaluation-set tuning |
| E | Outcome/route attribution contract, then shadow P/N/confidence policy | Identical events replay; negative feedback works; unknown outcomes do not punish; priors remain distinct |
| F | Varied local learning followed by untouched validation | Transfer to unrewarded evidence with declared collateral tolerance; no claim of maturity from repetition |
| G | Private-overlay/shared-concept experiment with realistic data | Transfer without payload crossover, plus separate privacy/abuse gates |

Recommendation: build B before C; if B implicates packing or another defect first, repair that
specific defect rather than insisting on seed relevance. Freeze a development comparison plan
before results. Preserve the six untouched LoCoMo histories for later validation, not repeated
tuning. Additional checks for normalization invariance, lone-edge confidence, saturation ties,
ID-order sensitivity and contributor-cap/decay dynamics belong in small deterministic math tests.

This is an algorithm/policy repair track, not a replacement of the core architecture. Local
deployment recovery work remains a separate track. Procedures, multimodal consumers, collective
learning and small-model uplift remain preserved hypotheses with independent gates.

## 7. Executed route tracing — step B

`RetrievalService(diagnostic_sink=...)` is a local, default-off diagnostic hook. It emits numeric
relationship contributions (related-tag aggregate, co-use, adjacency, Mem0 support/path) and
eligible candidates' raw/normalized channel scores, pre-pack rank and selected rank. It does
not change ranking formulas, weights or candidate budgets. No endpoint enables it by default.
Trace output includes private graph IDs and must remain scoped/local. A sink exception propagates
in this explicit diagnostic mode. Route labels describe the serving algorithm, not proven causal
credit; related-tag routes remain an aggregate and supersession overrides are not route events.

Reproduction: `python -m scripts.trace_locomo_cases`. The script copies the saved frozen conv-30
database, uses cached features, runs only q9 and q42, checks returned atom IDs and final scores
against the saved report, and verifies the source database checksum. Output from this execution:
`data/results/locomo-learning-v1/trace-20260906T101305Z/report.json`.

Dance answer D1:19 was ranked **29 before packing**, final .452190:

| Component | Normalized score | Weighted contribution |
|---|---|---|
| Direct tags | 0 | 0 |
| Lexical | .428571 | .107143 |
| Semantic | .948294 | .284488 |
| Relationships | .504654 | .060558 |

The tenth selected item scored .591625, including a .225 direct-tag contribution; its semantic
score was only .781815. The wrong-actor top item scored .889330 with tag=.940191. This establishes
a fusion/ranking loss, not missing candidates or packing exclusion, for this particular case.

The answer already receives adjacency contributions .175+.35+.175+.35=1.05, Mem0 path/support
contributions totaling 2, and related-tag contribution .003156. One .175 adjacency contribution
comes from D1:17, the useful contextual turn. Increasing all routes indiscriminately need not
help because distractors receive similar structural support: the top item also gets adjacency
1.05 and Mem0 support 2. Their normalized relationship scores are almost identical.

Rome gold turns remain at pre/post-pack ranks 1 and 3. D15:1 receives .45 direct-tag contribution,
.202025 semantic contribution and .070759 relationship contribution. This confirms the need to
retain useful tag matching rather than categorically favor vectors in every query.

Next-step refinement: inspect direct-tag absence versus related-tag recognition of
`contemporary dance`, then compare a query-sensitive scoring/activation policy on development
data. Do not assume seed weighting alone fixes this case: its bottleneck includes direct-channel
fusion, and the route already exists. Use these evaluation cases as explanations, not a tuning
objective. Candidate recovery, contextual activation and fusion deserve separate small tests.

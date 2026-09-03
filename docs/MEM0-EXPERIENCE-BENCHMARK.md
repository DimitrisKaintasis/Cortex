# Mem0 entity graph experience benchmark

Date: 2026-09-03
Status: mechanical pass; supervised adaptation signal found; production promotion not justified

## Question

Does the provenance-preserving Mem0 entity graph improve retrieval before feedback, and does
explicit useful-outcome feedback make the graph better over repeated use?

## Controlled setup

- Six public LongMemEval development cases, matching the historical usage experiment.
- 184 source atoms from the untouched clean snapshot.
- Mem0 extraction: `google/gemini-3.1-flash-lite` through OpenRouter.
- Mem0 and admission embeddings: Qwen3 Embedding 0.6B on the Mac Mini.
- Retrieval embeddings: the frozen Harrier 0.6B vectors already stored with the corpus.
- Frozen six-case query tags and query vectors reused for every snapshot and policy branch.
- Top 10 retrieval; five positive-use rounds; six feedback events per round.
- Each feedback branch started from a byte-for-byte copy of the same cold graph.

Two feedback shapes were compared:

1. `first_relevant` rewards only the first returned result attributable to benchmark evidence.
2. `all_relevant` rewards each returned result that adds distinct benchmark evidence or a new
   answer session. Lineage-equivalent duplicates are not rewarded twice.

This is supervised adaptation on repeated development questions. It is not held-out
generalization and does not test answer generation.

## Cold graph

Mem0 produced 96 private entity atoms, 128 exact `SUPPORTED_BY` links, and 80 typed relationship
proposals, with zero quarantines. The unchanged guarded vector policy admitted none of the typed
proposals: 46 were held for review and 34 rejected. Their total active typed weight therefore
remained zero.

This is an important result rather than a reason to tune the threshold on the test set. The short
relationship-to-full-evidence representation produced lower cosine similarity on real
conversations than on the ten-case synthetic fixture. The next admission experiment must be
versioned and evaluated separately.

Even with typed edges inactive, entity support/co-reference paths improved hybrid turn MRR from
`0.413` on the native graph to `0.533` on the cold entity graph. Turn recall stayed `0.875`.

## Retrieval result

| State | Turn recall | Turn MRR | Useful context fraction |
|---|---:|---:|---:|
| Native hybrid graph | 0.875 | 0.413 | not recorded |
| Cold Mem0 entity graph | 0.875 | 0.533 | 0.300 |
| After 30 first-relevant uses | 0.875 | 0.533 | 0.300 |
| After 30 all-relevant uses | 0.875 | 0.575 | 0.300 |

The first-relevant policy did not improve hybrid retrieval. The all-relevant policy improved
hybrid MRR by `0.042` without reducing recall or useful context fraction.

The graph-only profile responded more strongly to all-relevant feedback:

| Graph-only metric | Cold | After 30 uses | Change |
|---|---:|---:|---:|
| Turn recall | 0.583 | 0.708 | +0.125 |
| Turn MRR | 0.311 | 0.422 | +0.111 |
| Useful context fraction | 0.250 | 0.310 | +0.060 |

The graph recall gain appeared after the first round. Graph MRR improved gradually, while hybrid
MRR did not move until round three. Vector-only retrieval remained unchanged, providing a useful
control that the stored corpus and frozen query vectors did not drift.

## Learning density

| After five rounds | First relevant | All relevant |
|---|---:|---:|
| Selected returned items | 30 | 90 |
| Feedback weight transitions | 550 | 1,445 |
| Distinct learned edges | 110 | 289 |
| Atom-tag transitions | 30 | 60 |
| Atom-link transitions | 0 | 110 |
| Tag-relation transitions | 520 | 1,275 |
| New `CO_USED` edges | 0 | 22 |
| Final `CO_USED` weight | 0.0 | 21.0 |
| Final tag-relation weight | 164.0 | 318.5 |

All immutable weight-ledger audits passed. Factual Mem0 relationship weight remained unchanged at
zero; experience created only behavioral `CO_USED`, atom-tag, and tag-relation updates.

## Interpretation

1. **The new entity/support structure helps before learning.** It improves first-use ranking
   without copying source tags onto entity atoms or activating uncertain typed relationships.
2. **Experience can improve the behavioral graph.** Distinct multi-evidence attribution improved
   graph recall, graph MRR, graph context quality, and hybrid MRR on the repeated tasks.
3. **Single-result feedback is insufficient for atom-to-atom learning.** It created no `CO_USED`
   edges and left graph/hybrid retrieval unchanged.
4. **The current update fan-out is too high.** Forty-four to forty-eight weight transitions per
   selected item is too diffuse for a convincing production learning rule. Most transitions are
   all-pairs tag relations.
5. **This is adaptation, not generalization.** Repeating the same questions can reward memorized
   routes. A held-out query set within the same namespaces is required before promotion.
6. **The typed Mem0 layer was not exercised.** The accepted safety gate correctly kept all 80
   proposals inactive. Semantic admission remains a separate prerequisite.

## Decision

Keep multi-evidence attributable feedback as the promising experimental path, but do not promote
the current fan-out policy. The next isolated learning experiment should credit only graph paths
actually used by selected evidence, cap per-outcome influence, and compare held-out queries plus
collateral false positives. In parallel, test a semantic reviewer for held Mem0 proposals rather
than lowering vector thresholds on this fixture.

## Policy iteration 1 — remove all-pairs tag learning

The first controlled optimization kept atom-tag reinforcement and `CO_USED` atom links but
disabled feedback-driven all-pairs tag relations. Both branches started from the same cold graph,
used `all_relevant` feedback, and reused the same frozen queries and vectors.

| After five rounds | All-pairs control | Atom + `CO_USED` | Change |
|---|---:|---:|---:|
| Hybrid turn recall | 0.875 | 0.875 | 0.000 |
| Hybrid turn MRR | 0.575 | 0.575 | 0.000 |
| Graph-only turn recall | 0.708 | 0.708 | 0.000 |
| Graph-only turn MRR | 0.422 | 0.422 | 0.000 |
| Graph-only useful context | 0.320 | 0.320 | 0.000 |
| Feedback transitions | 1,445 | 170 | -88.2% |
| Distinct feedback edges | 289 | 34 | -88.2% |
| Tag-relation transitions | 1,275 | 0 | -100% |
| Final tag-relation weight | 318.466 | 99.466 | unchanged from cold |

Every measured ranking, recall, and useful-context metric, including every round's graph and
hybrid MRR, was identical. The mean raw relationship score was slightly lower, as expected when
one scoring channel stopped accumulating weight, but this changed no returned quality metric or
ranking. The candidate retained all 60 atom-tag and 110 atom-link transitions, the 22 learned
`CO_USED` edges, and the final `CO_USED` weight of 21.0. All ledger audits passed.

This candidate therefore Pareto-dominates the all-pairs control on this development fixture: it
has the same observed retrieval result with 88.2% fewer state transitions and no broad tag-graph
growth. It is the preferred policy for the next experiment, not yet a production default. The
questions are repeated development questions, so the next iteration must isolate whether the
gain comes from atom-tag reinforcement, `CO_USED` links, or both, and then validate the winner on
held-out query variations and negative/collateral cases.

## Reproduction artifacts

- Fixture: `evals/mem0_experience_v1.json`
- Mem0 config: `evals/longmemeval_dev6_mem0_entity_config.json`
- Native report: `data/results/longmemeval-dev6-native-baseline-v1.json`
- First-relevant report: `data/results/longmemeval-dev6-mem0-experience-first-v1.json`
- All-relevant report: `data/results/longmemeval-dev6-mem0-experience-all-v1.json`
- Atom + `CO_USED` report:
  `data/results/longmemeval-dev6-mem0-experience-atom-co-used-v1.json`
- Cold snapshot: `artifacts/longmemeval-dev6-entity-cold-v1.sqlite3`
- Used snapshots: `artifacts/longmemeval-dev6-entity-first-used-v1.sqlite3` and
  `artifacts/longmemeval-dev6-entity-all-used-v1.sqlite3`
- Atom + `CO_USED` snapshot:
  `artifacts/longmemeval-dev6-entity-atom-co-used-v1.sqlite3`

Runtime reports, databases, and Mem0 working stores are deliberately ignored by Git.

# Semantic tag priors — opt-in implementation and development experiment

2026-09-06. Production graph unchanged; experimental settings not promoted.

`TagSimilarityCalibrationService` builds separate `semantic_similarity` relations among
accepted namespace tags. The canonicalizer's merge behavior is unchanged. Existing identities
above its .90 merge threshold are not retrospectively merged or linked by this service.
Pairs in [.70,.90) are sorted by similarity then IDs, accepted greedily with maximum degree 3,
and receive weight .25*cosine. Confidence=1 is a neutral multiplier, not a claimed probability;
cosine and its interpretation are explicitly stored in immutable calibration-signal metadata.
Retrieval reads these priors through its existing symmetric related-tag expansion formula.
Behavioral `co_occurs` relations remain separate and are still what usage learning updates.
The current bounded learner cannot directly inhibit this prior below zero or revise its
similarity confidence. Outcome-based suppression/replacement belongs to the documented support/
negative-evidence policy work; this change implements initialization, not that unfinished policy.
Similarity must not be promoted as factual hierarchy, entity identity, or verified usefulness.

Canonicalization catalog embeddings are reused when the catalog/cache key is identical.
No re-tagging, new prompt or alias merging is involved. An optional `similarity_calibration`
argument to `TagEnrichmentService` runs calibration after accepted attachments and on resume.
Unaccepted tag proposals remain quarantined. A later approval can be followed by explicit
namespace calibration. Existing-tag reuse is included, not only newly accepted identities.

Use the same repository and canonicalizer instances when wiring enrichment. This API is opt-in,
not enabled by CLI/server defaults. Each namespace is limited to 500 accepted tags and refuses
larger catalogs rather than silently claiming full coverage. A future indexed neighbor search
is needed for larger catalogs; this bounded pair comparison is not the global-scale solution.

Pair IDs are stable and calibration uses the existing atomic signal/weight-ledger transaction.
Replays, reversed pair order, different models or changed settings do not repeatedly add priors
to an existing pair. Provider refresh/replacement requires an explicit future migration policy.
Existing priors are retained as the catalog grows; degree bounds hold, but online selection is
history-dependent rather than a globally recomputed top-k graph. Run as one serialized namespace
calibration job: repository transactions preserve atomic writes but do not make concurrent
read-select-write degree allocation serializable across competing workers.

First fixed experiment: frozen conv-26 only, 26 existing development questions, cached query
features and the unchanged ranking/packing formulas. Missing tag-name vectors use the already
configured embedding API/model and are cached; no source reingestion or tag-generation calls.
Compare direct-source recall/MRR to saved unchanged hybrid and vector-only baselines, by group.
Require recall improvement and no MRR loss in either group before considering validation.
Do not tune against the previously inspected conv-30 examples. Inspect changed source text.

Run: `python -m scripts.run_tag_similarity_diagnostic`. Disposables, checkpoint and report:
`data/results/locomo-learning-v1/similarity-*`. Verify ledger, replay and source checksum.

Execution adjustment before any scores or API calls: conv-26 contains 516 tags, so the initial
500-tag bound stopped the run. The diagnostic explicitly uses catalog_limit=600 to include all
516; production/default limit remains 500. Thresholds, degree cap and prior weight did not change.

## Result

Completed: `data/results/locomo-learning-v1/similarity-20260906T104402Z/report.json`.
3,911 pairs were in the configured band; the degree bound admitted 625 priors. Replay created
zero additional priors, the weight-ledger audit passed, and the original checksum held.
The unchanged-catalog pair matrix is cached to avoid repeating pair math on resumed documents.

| Metric group | Baseline | Similarity priors |
|---|---|---|
| Shared recall / MRR, 11 questions | .636364 / .558442 | .636364 / .558442 |
| Disjoint recall / MRR, 15 questions | .466667 / .167222 | .466667 / .167222 |

All 26 top-10 ID lists remained identical. Returned relationship scores changed (maximum absolute
delta .025289), so the new relation type was active, but it did not change ordering in this run.
Reported incremental embedding cost was $0.00001825 across 17 calls/1,825 tokens, for these tag
vectors only, not the original ingestion or earlier experiments.

Manual pair review: strong accepted pairs include interpersonal support/social support,
family activities/family recreation, and art review/art critique. Near the lower threshold,
exercise benefits/fitness gear and inspirational music/popular music are much looser associations.
Similarity remains a bounded prior rather than verified task usefulness. No changed retrieval
outputs existed to inspect; prior manual reviews remain applicable to the identical returned items.

Decision: implementation remains opt-in; no parameter or live corpus promotion. This one
band/strength did not improve retrieval. Next investigate route-specific selectivity and broad/
specific semantics rather than blindly increasing edge density or prior magnitude. The separate
negative-evidence/credit policy remains unfinished.

## Fixed strength follow-up (authorized after the first result)

Compare 1x, 3x, 10x of ONLY the 625 existing semantic-similarity prior weights, on fresh copies
of the calibrated development database. Keep all 26 conv-26 evaluation questions, query features,
other weights, candidate limits, ranking and packing fixed. No API calls or new edges. Actual
weights at 10x are about 1.75..2.25, below the safety ceiling 10. No clipping is expected.
These are explicit disposable aggregate overrides, not ledger-consistent new feedback events.
Check original checksum, unchanged non-similarity relations during scaling, and 1x replay.
Record exact-source recall/MRR, per-query gains/losses, pre-pack gold ranks and bridge contributions.
Inspect a gain and loss if present; otherwise inspect an affected gold route and competing item.
No repeated multiplier search after this run; mixed/null results send work back to route selectivity.
Run: `python -m scripts.run_similarity_strength`.

### Strength follow-up results

Completed artifact: `data/results/locomo-learning-v1/similarity-strength-20260906T155129Z/report.json`.
All 78 retrievals finished. 1x reproduced saved returned IDs and relationship scores; original
checksum and the non-similarity-relation scaling check passed. No API calls were made.

| Prior multiplier | Shared recall / MRR | Disjoint recall / MRR | Changed top-10 lists vs 1x |
|---|---|---|---:|
| 1x | .636364 / .558442 | .466667 / .167222 | 0 |
| 3x | .636364 / .558442 | .466667 / .167222 | 1 |
| 10x | .636364 / .558442 | .466667 / .166296 | 7 |

No question gained recall or first-relevant-result rank. At 10x one disjoint question lost one
rank; all other per-question recall/MRR values were unchanged. Changes to other result positions
do not imply unchanged results overall, even where these relevance metrics stay fixed.

Manual review and route tracing:

* q116 asks what inspired Caroline's art-show painting. The answer explicitly says a visit to
  an LGBTQ center and its unity/strength. It falls from rank 8 to 9 at 10x. A generic message
  about being busy painting rises from 9 to 8 through art inspiration -> visual arts and
  painting -> visual arts, whose priors rise from about .20 each to 2.0 each. The generic
  message's final score rises .591216 -> .594821; the actual answer stays at .592245 and has
  no direct similarity bridge from these query tags. This is topical overpromotion, not a
  useful answer appearing earlier.
* q7 asks Caroline's relationship status. The labeled single-parent passage has a family
  relationships -> family formation bridge. At 10x its raw relationship score rises
  1.059185 -> 1.141852 but final score only .355658 -> .357453; pre-pack rank improves
  180 -> 179, still far outside retrieval. There is a route, but stronger support alone
  cannot close its ranking gap under the present scoring mechanism.
* q93's necklace-from-grandmother answer already ranks first and has a personal history ->
  family history bridge. A route to already-successful evidence is not evidence of new benefit.

Decision: no multiplier is promoted; end the strength-only sweep as planned. The evidence points
to route selectivity/credit and score transformation rather than insufficient magnitude alone.
It does not establish that vector priors are useless or that mature graphs cannot transfer.
Next isolate the query's requested relation (e.g. inspiration, not just painting topic), broad/
specific routing, and how expanded-tag scores are aggregated, with controlled examples before
another real-data policy change. Keep the accepted atom architecture and long-term scope intact.

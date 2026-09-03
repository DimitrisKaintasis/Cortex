# LongMemEval graph calibration and controlled-use comparison

Date: 2026-09-03  
Status: six-case development experiment complete

## Question

Does joint Mem0 and vector bootstrap improve the tag graph, and do a few subsequent use events
move retrieval in the intended direction?

## Controlled scope

- Six public LongMemEval development cases, one from each represented question category.
- Mem0 extraction: `google/gemini-3.1-flash-lite` through OpenRouter.
- Corroboration and retrieval vectors: Harrier 0.6B F16 on the Mac Mini.
- Frozen query tags and query vectors from the earlier 20-case ablation.
- Top 10 retrieval.
- The original 20-case database was not mutated. Baseline, calibrated, and used states are
  separate SQLite snapshots.

The use simulation is deliberately supervised: hybrid retrieval exposes candidates, and the
public benchmark evidence labels reward the first relevant returned item. This measures whether
the existing feedback machinery can adapt to known outcomes. It does not measure held-out
generalization or justify the current feedback policy as a production design.

## Graph growth after joint calibration

| Property | Before | After | Change |
| --- | ---: | ---: | ---: |
| Atoms | 184 | 333 | +81.0% |
| Mem0-derived atoms | 0 | 149 | +149 |
| Atom-tag edges | 949 | 1,990 | +109.7% |
| Tag relations | 1,969 | 2,356 | +19.7% |
| Mean tag-relation weight | 0.0505 | 0.2568 | 5.08x |
| Median tag-relation weight | 0.0464 | 0.1430 | 3.08x |
| P95 tag-relation weight | 0.1287 | 1.1288 | 8.77x |
| Maximum tag-relation weight | 0.2805 | 2.3347 | 8.32x |

The run created 4,522 joint calibration weight events. The graph did not merely gain a few
high-confidence relationships: many Mem0 facts inherited several source tags, and every pair of
those tags received relationship evidence. This is the dominant density risk.

## Retrieval results

Lineage metrics credit a returned Mem0 memory when its exact `SUPPORTED_BY` source lineage
contains benchmark evidence. Direct metrics credit only returned raw atoms. Both views matter:
lineage measures whether the derived memory is useful, while direct retrieval verifies that source
evidence remains available.

| State/profile | Lineage turn recall | Lineage turn MRR | Direct turn recall | Direct turn MRR |
| --- | ---: | ---: | ---: | ---: |
| Before: tags only | 0.500 | 0.222 | 0.500 | 0.222 |
| Before: graph only | 0.583 | 0.222 | 0.583 | 0.222 |
| Before: vectors only | 0.750 | 0.542 | 0.750 | 0.542 |
| Before: direct hybrid | 0.875 | 0.413 | 0.875 | 0.413 |
| Calibrated: tags only | 0.500 | 0.417 | 0.333 | 0.139 |
| Calibrated: graph only | 0.583 | 0.417 | 0.417 | 0.139 |
| Calibrated: vectors only | 0.917 | 0.646 | 0.750 | 0.458 |
| Calibrated: direct hybrid | 0.875 | 0.410 | 0.875 | 0.388 |
| After 30 positive uses: tags only | 0.500 | 0.250 | 0.333 | 0.139 |
| After 30 positive uses: graph only | 0.583 | 0.333 | 0.583 | 0.156 |
| After 30 positive uses: vectors only | 0.917 | 0.646 | 0.750 | 0.458 |
| After 30 positive uses: direct hybrid | 0.875 | 0.435 | 0.875 | 0.385 |

## What the results mean

1. **Mem0 adds useful evidence.** Graph-only lineage MRR rose from `0.222` to `0.417`, and
   vector-only lineage recall rose from `0.750` to `0.917`. The memories often represented the
   correct evidence and appeared earlier than the raw source.
2. **The bootstrap is too dense.** Raw graph recall and MRR fell because numerous similarly tagged
   derived memories competed with source atoms. Relation-weight growth was much larger than
   relation-count growth, showing repeated reinforcement of broad shared pairs.
3. **Source preservation still works.** Raw atoms and exact Mem0-to-source support links remain in
   the database. The loss is ranking displacement, not destructive replacement.
4. **Existing usage learning is weakly discriminative.** Thirty positive feedback instances
   generated 725 edge transitions, about 24 per use. Only the fifth round changed graph recall.
   Graph direct recall recovered to its original level, but graph lineage MRR worsened.
5. **Hybrid retrieval absorbed the signal better than graph-only retrieval.** After five rounds,
   hybrid lineage MRR reached `0.435`, modestly above both its calibrated value (`0.410`) and its
   pre-Mem0 value (`0.413`). Its direct MRR remained below baseline.
6. **Naive corrective feedback cancelled or diffused.** A separate three-round run that rewarded
   one relevant item and penalized one irrelevant item per query created 880 transitions without
   changing any retrieval metric.

## Recommended next change

Do not scale the current all-pairs proposal rule. Preserve joint Mem0/vector calibration, but make
its evidence mass selective and bounded:

1. Filter inherited tags against each Mem0 fact using fact-to-tag vector agreement and relative
   ranking, rather than attaching every tag inherited from its support atom.
2. Normalize a fact's total relationship contribution across its accepted tag pairs. A fact with
   twelve tags must not carry sixty-six times the relationship mass of a two-tag fact.
3. Keep vector evidence as corroboration; it may prune or reduce an unsupported Mem0 pair but may
   not invent a typed relationship.
4. Preserve a raw-source quota or collapse lineage-equivalent derived candidates during packing,
   preventing correct Mem0 memories from crowding out all source evidence.
5. When usage learning is revisited, make it path- and rank-aware and normalize its update budget.
   The current broad pairwise update changes too many relationships per outcome.

## Artifacts

- Full positive-use report: `data/results/longmemeval-dev20-graph-calibration-positive-usage-lineage-v1.json`
- Corrective-use report: `data/results/longmemeval-dev20-graph-calibration-usage-v1.json`
- Baseline snapshot: `artifacts/longmemeval-dev20-baseline-comparison-v1.sqlite3`
- Joint-calibrated snapshot: `artifacts/longmemeval-dev20-joint-calibration-v1.sqlite3`
- Five-round positive-use snapshot: `artifacts/longmemeval-dev20-joint-positive-used-lineage-v1.sqlite3`

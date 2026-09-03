# Mem0/vector cold-start experiment

Date: 2026-09-03
Status: implementation passed; automatic promotion failed

## Purpose

This is the safe replacement for the removed Mem0/vector all-pairs bootstrap. Mem0 proposes a
typed entity edge with exact evidence. Vectors score only that proposal against its attributed
evidence. They never search the corpus for arbitrary pairs or generate a predicate.

New proposals start inactive:

```text
Mem0 relationship + exact evidence
    -> unreviewed edge, weight 0
    -> relation/evidence vector similarity + explicit endpoint check
         -> provisional: bounded weight <= 0.25
         -> hold_for_review: weight 0
         -> rejected: weight 0
```

Run calibration for one isolated namespace:

```powershell
python -m data_retrieval calibrate-mem0-vectors `
  --db .\data.sqlite3 `
  --namespace <namespace> `
  --embedding-model qwen3-embedding:0.6b `
  --ollama-url http://127.0.0.1:11435
```

The command records immutable calibration signals and is replay-safe. Threshold and cap flags are
available for explicit experiments; changing them creates a different policy profile.
Bootstrap profile v5/entity profile v2 also provide the upgrade boundary: rerunning an older
completed batch reimports its relationship as an inactive proposal before calibration.

Compare a frozen real Mem0 proposal report without rerunning extraction:

```powershell
python -m data_retrieval evaluate-mem0-cold-start `
  --fixture .\evals\mem0_entity_quality_v1.json `
  --mem0-report .\data\results\mem0-entity-quality-v1.json `
  --embedding-model qwen3-embedding:0.6b
```

## First result

| Profile | Typed edges | Correct | Precision | Recall | Incorrect active weight |
|---|---:|---:|---:|---:|---:|
| Baseline | 0 | 0 | n/a | 0% | 0 |
| Vectors only | 0 | 0 | n/a | 0% | 0 |
| Mem0 only | 14 | 9 | 64.3% | 75.0% | 5.000 |
| Mem0 + vector provisional | 11 | 8 | 72.7% | 66.7% | 0.527 |
| Provisional + perfect review of held edges | 12 | 9 | 75.0% | 75.0% | 0.527 |

The mechanism behaved correctly and reduced incorrect weight by 89.5%. The policy did not pass
promotion: topical similarity could not detect three semantically false but lexically explicit
edges. The oracle row demonstrates that reviewing only held proposals is insufficient because
some bad proposals already passed the cheap gate.

This result does not measure end-to-end retrieval. It establishes that vectors are useful for
bounded weighting and review priority, but insufficient for semantic admission. The next test must
add a selective semantic validator and then compare baseline, vectors, Mem0 paths, and the guarded
hybrid on retrieval outcomes.

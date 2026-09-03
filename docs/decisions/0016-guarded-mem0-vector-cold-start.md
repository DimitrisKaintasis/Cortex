# ADR-0016: Guarded Mem0/vector cold-start calibration

## Status

Accepted for isolated testing. Automatic promotion to full-strength serving edges is not
accepted.

## Context

ADR-0015 replaced the rejected Mem0 fact/tag all-pairs bootstrap with private entity atoms,
typed entity relationships, and exact source-atom provenance. The first real-model quality gate
showed that provenance was exact but `gemma4:e2b-mlx` relationship proposals were too noisy for
unchecked weight updates.

Vectors are useful evidence of topical alignment, but cosine similarity cannot prove predicate
direction or factual truth. A false relationship can be highly similar to the sentence from which
it was generated. Therefore neither Mem0 nor vectors may independently create a trusted edge.

## Decision

- Persist a new provenance-valid Mem0 relationship proposal with active weight `0` and
  `admission_state=unreviewed`.
- Keep the raw Mem0 confidence as proposal metadata rather than silently activating it.
- Run vector calibration only over Mem0-proposed endpoint pairs and their exact supporting atoms;
  never perform corpus-wide or tag-all-pairs comparisons.
- Require both endpoint names to appear explicitly in the attributed evidence for automatic
  provisional admission. Pronoun/coreference cases are held for review.
- Reject vector similarity below `0.60`, hold `0.60–0.80` for review, and allow similarity at or
  above `0.80` to create only a bounded provisional weight.
- Cap provisional weight at `0.25`; scale it by vector similarity and proposal confidence.
- Store every decision as an immutable, replay-safe calibration signal with model and policy
  provenance.
- Version the bootstrap/import profiles. Reprocessing an older Mem0 entity link resets its legacy
  active weight to zero before the new admission policy can act.
- Keep rejected and held relationships at weight `0`. Retrieval ignores zero-weight relationship
  links.
- Treat all constants as the `mem0-vector-cold-start-v1` experimental profile. Do not tune them on
  the ten-case fixture and call the result general.

## First experiment

The frozen ten-case e2b proposal report contained 14 proposed edges for 12 expected relationships.
Qwen3 Embedding 0.6B produced:

| Profile | Active typed edges | Correct | Precision | Recall | Incorrect weight |
|---|---:|---:|---:|---:|---:|
| Mem0 only at weight 1 | 14 | 9 | 64.3% | 75.0% | 5.000 |
| Mem0 + vector provisional | 11 | 8 | 72.7% | 66.7% | 0.527 |
| Provisional + perfect review of held edges | 12 | 9 | 75.0% | 75.0% | 0.527 |

The gate reduced incorrect active weight by 89.5%, but it did not meet the 90% precision/recall
promotion target. Three false proposals were topically similar and explicitly mentioned both
endpoints, so vector corroboration admitted them. Even a perfect reviewer of only the held cases
could not repair false proposals already admitted automatically.

## Consequences

- The cold-start machinery is now safe to test on snapshots: raw proposals are inactive and
  admitted influence is small, versioned, and reversible.
- Vector corroboration is retained as a strength/risk feature, not treated as a truth oracle.
- The current policy must not be promoted to unattended large ingestion.
- The next experiment needs an additional semantic check for apparently high-similarity proposals,
  followed by end-to-end retrieval comparison. A local 12B fallback is unavailable on the current
  Mac due to MLX memory failures.

# Development fusion diagnostic

Fixed before execution, 2026-09-06. Production defaults remain unchanged.

Observation: q42 query tags include dance, dance performance and competition experience;
answer tags are contemporary dance and performance art. These are related meanings, not
necessarily aliases. Exact direct matching gives zero; relationship expansion recognizes
contemporary dance. Do not merge catalog concepts to repair a single evaluation question.

Experiment: conv-26 development only, all 26 existing evaluation questions, frozen graph.
Reuse query features and the full eligible candidate pool from unchanged retrieval. Verify
baseline IDs and scores exactly. Re-rank that pool and apply the unchanged evidence packer.
No learning updates, model calls, temporal changes, new candidates or evaluation-history tuning.

One fixed candidate policy: g = overlap(tag top-10, semantic top-10)/size(tag top-10), using
positive-score candidates. Tag weight=.45*g, semantic weight=.30+.45*(1-g), lexical=.25,
relationship=.12. Absent semantic evidence leaves g=1. The runner requires all three base
channels to be active. This is channel-agreement gating, NOT probability calibration: vectors
can be wrong, and disagreement can identify complementary useful tags rather than bad tags.
No threshold sweep or graph expansion change is included. Selection is candidate-pool conditional.

Compare direct-source recall/MRR with unchanged hybrid and saved vector-only results, separately
for shared/disjoint evidence. Report individual gains/losses and manually inspect changed outputs.
A promising configuration needs recall improvement without MRR regression in either group before
considering untouched validation; a single development history cannot establish generalization.
If mixed or negative, record that outcome and do not promote or tune repeatedly on these questions.

Run: `python -m scripts.run_fusion_diagnostic`. Local disposable output and progress live under
`data/results/locomo-learning-v1/fusion-*`. Source checksum is checked at completion.

## Results — completed 2026-09-06

Artifact: `data/results/locomo-learning-v1/fusion-20260906T101952Z/report.json`.
All 26 baseline rankings and final scores reproduced. Source checksum remained unchanged.
No production policy changed. Three deterministic policy tests passed; changed files pass Ruff.

| Policy | Shared recall / MRR (11) | Disjoint recall / MRR (15) |
|---|---|---|
| Unchanged hybrid | .636364 / .558442 | .466667 / .167222 |
| Agreement gate | .636364 / .530303 | .466667 / .222222 |
| Saved vector-only, direct source | .500000 / .321212 | .666667 / .386746 |

Shared MRR: 1 question improved, 1 worsened. Disjoint MRR: 3 improved, 3 worsened.
Disjoint recall: 2 improved and 2 worsened, canceling in the mean. Shared recall did not change
on any question. Thus unchanged average recall does not mean identical evidence coverage.
Gate failed: shared ranking regressed and neither group's average recall improved. Do not promote.

Manual review of the first rank loss and first rank gain in saved order:

* conv-26-q120 asks whose birthday Melanie celebrated. The explicit daughter's-birthday passage
  falls from rank 1 to 3. Gate=0; the new top results are a generic family-outing message and a
  friendship thank-you. Neither answers the question. Tag/vector disagreement incorrectly
  removed useful tag evidence here.
* conv-26-q12 asks how long ago Caroline's 18th birthday was. The passage explicitly saying
  "ten years ago" rises from rank 7 to 2. Gate=0 again. The new rank-1 item concerns doing art
  since about age 17 and is not the birthday answer. This is a real ranking gain, not a complete
  answer-selection success. No temporal engine or generated answer was evaluated.

Conclusion: disagreement alone cannot tell complementary evidence from unreliable evidence.
This heuristic is not calibrated uncertainty, and the two inspected gate=0 cases demonstrate
opposite outcomes. Do not respond by repeatedly searching overlap thresholds on this history.

Next recommended work: a controlled, separately testable broad-to-specific tag routing and
query-weighted context propagation fixture, preserving direct tag identity and provenance.
Evaluate whether existing relations allow a query concept to reach a specific answer tag without
promoting every loosely associated atom. Keep fusion calibration and route learning distinct;
do not infer that this failed fusion heuristic disproves query-sensitive graph navigation.

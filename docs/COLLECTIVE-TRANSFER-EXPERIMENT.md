# Collective Transfer Experiment v1

Status: deterministic mechanism test passed; production decision not reached  
Fixture: `evals/collective_transfer_v1.json`  
Command: `python -m data_retrieval evaluate-collective-transfer`

## Question

Can outcomes from private user A improve private user B's ranking through shared concept
relationships without transferring A's private evidence?

This is the smallest test of the project's central collective-learning claim. It is deliberately
separate from `GlobalAblationRunner`, which puts LongMemEval data in one shared namespace and
does not model private overlays or privacy-safe contribution.

## Data flow under test

```text
A private outcome
  -> minimized concept-to-concept observation
  -> idempotent contributor-bounded aggregation
  -> immutable shadow snapshot
  -> relative bonus applied to B's private candidate IDs
```

The ranker receives B's baseline scores, candidate IDs, and mappings to stable shared concept
IDs. It does not receive candidate payloads. A relationship observation contains an observation
ID, opaque epoch-scoped contributor bucket, source and target shared concept IDs, outcome,
support, time, and policy version. It contains no atom, document, namespace, query, session,
source path, public user ID, or payload field.

## Result

The deterministic suite passes 18 of 18 checks:

- B's correct PostgreSQL candidate starts at rank 2.
- A's positive storage-to-PostgreSQL observation moves it to rank 1.
- A's verified negative observation reduces that influence to zero and returns it to rank 2.
- An unrelated remote-inference query is unchanged.
- Removing the snapshot restores the exact baseline.
- Observation order and duplicate delivery do not change the snapshot.
- One contributor's repeated support is capped at `1.0`; a second independent contributor can
  raise aggregate support to `2.0`.
- Thirty days reduces a test observation from `0.3` to `0.15`; prolonged non-use becomes
  dormant; renewed independent evidence reactivates it.
- Equal verified negative support can inhibit the relationship without deleting its history.
- Private sentinels and forbidden payload fields do not appear in the public experiment output.

The generated local report is `data/results/collective-transfer-v1.json`. The `data` directory
is intentionally ignored because reports contain environment-specific revision, dirty-state,
duration, and future model/provider details. The fixture, implementation, tests, and this summary
are the checked-in reproducible evidence.

## Policy ablation result

| Policy | Positive target rank | After negative outcome | Interpretation |
|---|---:|---:|---|
| No collective learning | 2 | 2 | Control behaves as expected |
| Original bounded `0..1` approximation | 2 | 2 | The `0.5` prior compresses the useful difference in this fixture |
| Unbounded, no decay | 1 | 2 | Transfers the desired signal |
| Unbounded, proportional decay | 1 | 2 | Same immediate result; lifecycle checks verify decay separately |
| Unbounded, separate positive/negative | 1 | 2 | Transfers while retaining auditable opposing support |
| Maximum reference plus weight-100 outlier | 2 | 2 | A single extreme edge suppresses the useful neighborhood signal |
| Median/percentile reference plus the outlier | 1 | 2 | Robust reference preserves the useful signal in this fixture |

These results reject copying the original bounded constants or maximum-reference normalization
without broader evaluation. They do not yet select a final decay half-life, contributor cap,
negative penalty, percentile, or serving bonus.

## What this proves—and what it does not

It proves that the proposed minimized observation and shadow-snapshot mechanism can produce
positive and negative cross-user ranking transfer deterministically, with replay, isolation,
basic contributor bounds, and lifecycle behavior.

It does not prove:

- that model-generated tags align different users to the correct shared concepts;
- that real tasks improve across a varied corpus;
- that rare concepts cannot reveal sensitive interests;
- that opaque contributor buckets are unlinkable;
- that sentinel absence constitutes formal privacy;
- resistance to coordinated Sybil contributors or sophisticated poisoning;
- production consent, sensitivity classification, erasure, secure aggregation, or database
  behavior;
- that any tested constant is ready for global serving.

The architecture checkpoint therefore remains **revise/continue experimenting**, not
**proceed to production**.

## Next experiment

Build a deterministic fixture matrix rather than adding infrastructure:

1. many source neighborhoods with balanced, skewed, and heavy-tailed relationships;
2. multiple A/B tasks, irrelevant concepts, ambiguous mappings, and conflicting contributors;
3. minimum independent-contributor thresholds and coordinated-contributor attacks;
4. time slices that distinguish no decay, proportional decay, and the historical discrete
   decay control;
5. percentile and clipped-reference sweeps with unrelated-regression metrics;
6. explicit acceptance thresholds for target lift, false positives, contributor concentration,
   leakage probes, and rollback.

Only after one policy wins that matrix should we make the Step 6 proceed/revise/reject decision
or modify canonical storage contracts.

# Selective AI Review Cascade v1

Status: deterministic orchestration test passed; real-model quality unmeasured  
Fixture: `evals/review_cascade_v1.json`  
Command: `python -m data_retrieval evaluate-review-cascade`

## Decision under test

Most observations should use cheap deterministic controls. Expensive AI review should run only
when a payload-free gate detects enough uncertainty, expected impact, or risk. This experiment
compares:

1. cheap rules only;
2. AI review for every entry;
3. the gated cascade.

The experiment is isolated from normal retrieval and databases. Review verdicts are fixed
fixture outputs, not real model calls, so the result tests routing, cost accounting, auditability,
and weight boundaries rather than model intelligence.

## Implemented boundary

The cheap gate consumes only bounded features:

- uncertainty and expected ranking impact;
- risk, positive/negative conflict, and contributor concentration;
- concept rarity and alignment confidence;
- novelty, sensitivity, and candidate-snapshot regression flags.

It returns `apply_cheap`, `review_async`, or `hold_for_review`, plus a priority and reason codes.
Escalated updates are not applied before review; the current active snapshot continues serving.
A budget planner selects the highest-priority pending reviews.

The expensive reviewer returns a structured event containing verdict, confidence, recommended
strength, reason codes, reviewer family/profile, time, shared concept IDs, and an evidence
digest. It contains no payload or hidden reasoning. `uncertain` and `abstain` produce no weight
observation.

Accepted reviews enter an `ai_review` evidence channel separate from ordinary `behavioral`
support. Policy—not the model—converts confidence, strength, and historical reviewer reliability
into a capped number of equivalent support units. Repeated reviews from the same reviewer family
and aggregation epoch share one cap and are not independent evidence.

## Result

The 13-scenario fixture contains ten routine cases and three cases where cheap judgment is wrong:
a high-impact conflict, a novel uncertain concept alignment, and a sensitive rare concept.

| Strategy | Correct decisions | Expensive reviews | Relative cost units |
|---|---:|---:|---:|
| Cheap only | 10/13 | 0 | 0.13 |
| Review everything | 13/13 | 13 | 13.13 |
| Gated cascade | 13/13 | 3 | 3.13 |

The cascade matched the fixed review-everything result while spending about 24% of its relative
cost. Seventeen of seventeen orchestration checks passed, including:

- routine observations remain on the cheap path;
- conflict and sensitive cases are held for review;
- uncertain novel alignment is escalated;
- a two-call budget chooses the sensitive and conflict cases first;
- AI review can outweigh one behavioral observation but is capped at five equivalent units;
- repeated reviews from the same reviewer remain capped;
- behavioral and review support remain separately visible;
- a strong negative review can inhibit rather than delete a relationship;
- abstention changes no weight;
- review exports contain no payload or hidden reasoning fields.

## Interpretation

This validates the proposed cascade as an architectural mechanism. It shows that selective
review can, in a separable controlled fixture, preserve review-everything decisions at much lower
call volume while keeping AI influence bounded and auditable.

It does not show that a real cheap gate will reliably predict when a real model adds value. The
fixture's labels and review outputs were designed in advance. It also does not establish the
five-unit cap, triage thresholds, model reliability, privacy of evidence digests, or provider
costs as production values.

## Next validation

1. Expand to overlapping cases around the triage threshold, including false-positive and
   false-negative escalations.
2. Run one small local model and one stronger reviewer on the same frozen evidence packets.
3. Measure decision quality, calibration, latency, cost, and how often the strong reviewer
   actually changes the cheap decision.
4. Train or tune the cheap gate only on a development split; preserve a frozen evaluation split.
5. Test prompt injection, sensitive payload redaction, model-family correlation, repeated
   reviews, provider failure, and queue-budget exhaustion.
6. Keep review asynchronous and in shadow mode until the cascade beats cheap-only behavior
   without unacceptable missed escalations or unrelated regression.

The policy-selection and adversarial roadmap steps remain in progress.

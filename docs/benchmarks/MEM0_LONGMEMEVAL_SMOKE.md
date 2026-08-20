# Mem0 + LongMemEval smoke benchmark

Date: 2026-08-20

## Purpose

Verify that already-ingested native atoms can be distilled by a small model on the Mac Mini,
imported with source lineage and calibration signals, resumed safely, and retrieved through the
normal native pipeline. This is an engineering smoke test, not a statistically meaningful model
benchmark.

## Configuration

- Dataset: `longmemeval_oracle.json`, hash `821a2034d219...`
- Cases: 3 (`multi-session`, `single-session-assistant`, `temporal-reasoning`)
- Raw input: 4 sessions, 30 atoms
- Mem0 LLM on Mac: `qwen3.5:4b`
- Embeddings on Mac: `qwen3-embedding:0.6b`
- Working/canonical benchmark store: fresh laptop-local SQLite and Qdrant files
- Retrieval: top 10, embeddings enabled, tag and Temporal enrichment disabled

## Calibration result

The one-pass final extraction returned and imported 50 memories. It created 410 source-lineage
links and 540 total calibration signals. An immediate replay resumed all 4 source batches, made no
model calls, and created no additional memories, links, or signals.

Examples of correctly distilled evidence include:

- New York City trip lasting five days
- prior family trip lasting 10 days and the associated Hawaii trip
- Dr. Arati Prabhakar as the President's Chief Advisor for Science and Technology
- networking event from 6 PM to 8 PM on the source-relative "today"

## Retrieval comparison

| Metric | Raw baseline | After one Mem0 pass |
| --- | ---: | ---: |
| Session hit@10 | 1.000 | 1.000 |
| Session recall@10 | 1.000 | 1.000 |
| Turn hit@10 | 1.000 | 1.000 |
| Turn recall@10 | 0.889 | 0.778 |
| Mean reciprocal rank | 1.000 | 1.000 |
| Multi-session turn recall@10 | 0.667 | 0.333 |
| Assistant-fact turn recall@10 | 1.000 | 1.000 |
| Temporal-reasoning turn recall@10 | 1.000 | 1.000 |

Latency is omitted from the comparison because the tiny local run mixed cold and warm model/cache
states. It is not a valid performance measurement.

## Interpretation

The bootstrap pipeline works and the small model captures the target facts. The unrestricted
retrieval mix does not yet improve this sample: derived memories consume top-k slots that would
otherwise contain additional raw evidence from the multi-session case. First-hit quality remains
perfect, but evidence breadth falls.

Do not solve this by weakening Mem0 extraction or deleting provenance. The next retrieval change
should explicitly diversify raw and derived evidence, collapse lineage-equivalent candidates, or
reserve part of the result budget for raw sources. That behavior needs its own before/after test on
a larger representative sample before becoming the default.

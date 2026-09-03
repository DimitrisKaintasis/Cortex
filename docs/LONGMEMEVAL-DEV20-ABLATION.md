# LongMemEval Dev20 retrieval ablation

Updated: 2026-09-03  
Status: complete; exploratory oracle-slice result, not a production benchmark

## Experimental controls

- Dataset: the same 20-case stratified slice of `longmemeval_oracle.json`
- Canonical database: `artifacts/longmemeval-dev20-clean-v1.sqlite3`
- Frozen query features: `data/results/longmemeval-dev20-query-features-v1.json`
- Full report: `data/results/longmemeval-dev20-ablation-v1.json`
- Query tags: one cached Gemini 3.1 Flash Lite generation per question
- Query vectors: one cached Harrier vector per question
- Cutoffs: top 3, top 5, and top 10
- Profiles: lexical only, vector only, tags only, lexical+vector, hybrid with
  direct tags, hybrid with tag relationships, and the full Temporal system

All 21 profile/cutoff evaluations reused identical query tags and vectors. Re-running the suite
from the cache makes no API or Mac embedding calls.

## Primary raw answer-turn results

| Profile | Recall@3 | Recall@5 | Recall@10 | Turn MRR@10 |
|---|---:|---:|---:|---:|
| Lexical only | 48.33% | 60.83% | 81.00% | 0.5608 |
| Vector only | **66.17%** | **75.33%** | **90.83%** | **0.8042** |
| Tags only | 42.67% | 54.33% | 59.33% | 0.4742 |
| Lexical + vector | 58.33% | 74.17% | 89.50% | 0.7133 |
| Hybrid + direct tags | 56.00% | 75.17% | 89.92% | 0.6388 |
| Hybrid + tag graph | 53.50% | 70.17% | 86.42% | 0.5838 |
| Full system, direct raw evidence | 49.33% | 61.00% | 86.42% | 0.4882 |
| Full system, Temporal lineage credit | 73.08% | 83.50% | 100.00% | 0.7125 |

The explicit turn MRR fields were added during this experiment. The older combined MRR accepts
either a relevant session or an answer-bearing turn. Because every supplied session in the oracle
dataset is relevant, that combined value can be perfect even when answer-turn retrieval is not.
Use `turn_mean_reciprocal_rank` and `direct_turn_mean_reciprocal_rank` for future comparisons.

## Interpretation

### Vectors currently carry retrieval quality

Harrier vector-only retrieval is the strongest direct answer-turn profile at every cutoff and has
the best direct turn MRR. Adding lexical scores reduces its early ranking quality. This means the
current fixed channel weights are not calibrated for this corpus; more channels are not
automatically better.

### Current direct tags are weak

Tags-only retrieval trails both lexical and vector retrieval. Adding direct tags to the
lexical-vector hybrid changes recall only marginally at top 5 and top 10 while reducing early MRR.
The likely causes include broad/generated query tags, fragmented per-case catalogs, and fixed tag
weighting. This result does not yet test a mature learned tag system.

### Current relationship expansion adds noise

Compared with the same hybrid using direct tags but no relationship expansion, the tag graph
reduces direct answer-turn recall by 2.50 points at top 3, 5.00 points at top 5, and 3.50 points at
top 10. These relationships were initialized from ingestion and have not accumulated the diverse
feedback, Mem0 calibration, contributor evidence, or decay history envisioned for a mature graph.
The present graph must therefore not be assumed beneficial merely because the architecture can
represent useful learned relationships.

### Temporal summaries trade raw slots for broad coverage

The full system's lineage recall substantially exceeds the no-summary tag-graph profile: 73.08%
versus 53.50% at top 3, 83.50% versus 70.17% at top 5, and 100% versus 86.42% at top 10. At top 5,
lineage recall reaches 80% for multi-session questions and 87.5% for temporal-reasoning questions.

However, exact raw-answer recall falls because summary atoms occupy limited result slots. Lineage
credit proves that a selected summary was derived from answer-bearing evidence; it does not prove
that the summary text retained the answer. Future evaluation needs summary answer-support or
faithfulness checks and a packing policy that can attach source evidence to a selected summary.

## Latency

With query features frozen, approximate local mean latency ranged from 7 ms for lexical retrieval,
23 ms for vector retrieval, about 70 ms for tags-only retrieval, and roughly 110-180 ms for graph
and full retrieval. The earlier 1.59-second end-to-end number included live remote query tagging
and query embedding. Query-feature preparation and local retrieval latency must remain separate in
future reports.

## Limitations

- Only 20 cases were evaluated.
- The oracle dataset contains only answer sessions, not irrelevant distractor sessions.
- Seventeen namespaces were enriched locally and three were recovered with Gemini.
- Query tags came from Gemini for every profile, while stored tags came from mixed enrichment.
- Mem0 calibration and accumulated user feedback are absent.
- The graph is sparse and immature, so this measures the current implementation rather than the
  long-term collective-learning hypothesis.
- No answer-generation or summary-faithfulness judge was run.

## Recommended next experiments

1. Diagnose per-channel scores and retrieved items for `6d550036` and the cases where tag or graph
   expansion displaces vector-ranked evidence.
2. Add a source-expansion packing policy: a selected summary should expose its strongest supporting
   raw atoms without silently receiving blanket lineage credit.
3. Add distractor sessions to a frozen development split, keeping a separate untouched test split
   so channel weights are not tuned and evaluated on the same 20 cases.
4. Bootstrap and measure Mem0 calibration as its own profile before including it in the full
   system.
5. Repeat the profile matrix after feedback-derived graph updates to test whether training changes
   the graph delta from negative to positive.

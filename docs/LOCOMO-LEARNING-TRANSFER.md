# LoCoMo learning-transfer pilot v1

## Plan fixed before scoring — 2026-09-05

This is a custom adaptation experiment using the public LoCoMo dataset, not an official LoCoMo
score. Source: https://github.com/snap-research/locomo, revision
`3eb6f2c585f5e1699204e3c3bdf7adc5c28cb376`. Dataset attribution: Maharana et al.,
*Evaluating Very Long-Term Conversational Memory of LLM Agents*, ACL 2024.
The repository license is CC BY-NC 4.0; raw data and derived runtime stores remain local/ignored.

1. **Data audit and split.** Use conv-26 only for development/smoke testing. Use conv-30,
   conv-41 and conv-42 as the fixed evaluation pilot. Do not select histories based on scores.
   Preserve all ten histories on disk; the other six remain unused. Admit questions in
   categories 1, 2, 4 only when all evidence IDs resolve to text-bearing turns and an answer exists.
   Exclude image-bearing evidence from this text-only experiment. Report all exclusion counts.
2. **Leakage controls.** One source atom per dialogue turn, with speaker/session/time preserved.
   Never ingest QA, answer labels, supplied observations, or supplied summaries as evidence.
   Exact official dialogue IDs define labels. Deterministically order eligible questions by a
   seeded SHA-256 hash; first ten per history provide feedback. From the remainder take up to
   fifteen each with any versus no evidence-ID overlap with the training-question labels.
   Freeze the manifest before ingestion/scoring. Reject duplicate normalized question text across
   splits. No paraphrase generation or judge-written expected answers.
3. **Identical initial graph.** Standard tag proposal/quarantine followed by the existing explicit
   benchmark catalog resolver at 0.65 confidence. Normal pinned Mem0 SDK with provenance adapter,
   then guarded vector admission using unchanged defaults. Freeze API query tags and embeddings.
   Use identical prepared snapshots for every branch; no model calls during scoring.
4. **Learning.** Three branches: frozen; atom-tag-only; existing query-to-evidence relation policy
   (atom tags + co-use + tag relations). Three rounds of ten training questions per history,
   top 10. Reward only exact returned source atoms listed in official training evidence;
   never reward a whole session or a derived item's broader support set merely because one turn
   is relevant. This keeps disjoint evaluation evidence out of feedback. Same feedback events/items
   from frozen retrieval are replayed into both learning branches to isolate update policy.
5. **Evaluation.** Evaluate untouched questions at cold and every round. Primary endpoint is
   macro evidence recall@10 on label-disjoint questions; secondary is MRR, also direct-source
   scores. Show each history separately, shared-evidence results separately, and per-query gains,
   losses, unchanged, training coverage, and actual feedback-credit overlap. No QA correctness
   claims: this first run measures evidence retrieval, not generated answers.
6. **Controls and costs.** Frozen retrieval repeated at end; vector-only baseline; immutable
   evidence/Mem0/vector fingerprints; weight-ledger audits. Record API usage where available,
   preparation/scoring latency, and graph updates. Do not tune thresholds/weights after scoring.
   A positive pilot requires improved disjoint recall over frozen without disjoint MRR loss,
   and no history-level recall regression; report atom-only comparison independently.
7. **Review and preserve.** Publish failures as well as gains. Three evaluation histories are
   clusters, not dozens of independent datasets. Preserve remaining histories for later validation.

All processing/storage is on laptop SQLite; API handles model calls on selected public data.
Canonical PostgreSQL, existing benchmark snapshots, the Mac, and production policies are untouched.
Infrastructure/model transport failures may be repaired without changing the fixed scoring protocol;
log such deviations. Stop rather than silently substitute a fake Mem0 output or skip failed batches.

## Results

Completed. Configuration: `evals/locomo_learning_v1.json`. The primary transfer gate did not pass.
Measured results, development-only 1x/3x/10x follow-up, interpretation limits, and next steps are
preserved in [the mathematical audit](LEARNING-RETRIEVAL-MATH-AUDIT.md#4-saved-experimental-evidence).
No tested multiplier was promoted to production.

The fixed audit selected 67 evaluation questions: 45 with training-label-disjoint evidence and
22 sharing evidence. The three evaluation histories contain 1,661 source turns. Development
conv-26 contributes 419 turns and 26 separate smoke questions, excluded from headline results.
The full preparation therefore contains 2,080 source turns across 99 sessions.
Invalid/missing evidence, duplicate question text, excluded categories, and image-dependent
evidence were excluded before scores were available. Detailed counts are in the fixed manifest.
Timestamps use an explicit UTC assumption because the dialogue dates provide no timezone.

### Preparation checkpoint and execution repairs

All 2,080 source-turn markers are complete. Mem0 imported 506 entity atoms and 490 relationship
proposals. The prepared store contains 2,586 atom embeddings and 133 frozen query feature files
(40 training, 26 development evaluation, 67 evaluation). Query tags and vectors are generated
before scoring; evaluation answers are not inputs to either generator.

| History | Provisional relationships | Held | Rejected |
|---|---:|---:|---:|
| conv-26 (development) | 8 | 86 | 28 |
| conv-30 | 1 | 40 | 33 |
| conv-41 | 0 | 62 | 81 |
| conv-42 | 8 | 97 | 46 |

These are the unchanged vector-admission policy's dispositions, not human-verified relationship
correctness. Every branch starts with the same dispositions and weights.

One 32-turn development batch returned no Mem0 outputs. The initial batch-marker check therefore
reported 2,048/2,080 although all source text had been sent through Mem0. The resumability wrapper
was corrected and the empty batch explicitly accepted; raw source atoms remain present. This
does not mean every turn generated a Mem0 entity. Self-referential proposals were also quarantined
in conv-30 and conv-42. No fake relationships were substituted.

Query preparation encountered malformed JSON from the API. Bounded retries were added. An
indentation error in that repair then allowed a premature completion marker with only 33/133
query caches. This was found before scoring; the loop was repaired and all missing query features
completed. A regression test now exercises two histories and verifies complete feature generation
and cache reuse. These repairs changed no selected question, scoring threshold, or learning policy.

Preparation cost is not comprehensively metered: the latest feature-stage usage report excludes
earlier attempts and the Mem0 SDK/tag-proposal calls. Do not present it as total benchmark cost.

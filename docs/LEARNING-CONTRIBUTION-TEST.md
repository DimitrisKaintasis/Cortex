# Learning contribution test v1

## Protocol fixed before scoring — 2026-09-05

Question: does outcome learning improve retrieval beyond an identical Mem0/vector starting
point? This is a small diagnostic, not a showcase-selected success or a Mem0 competitor benchmark.

- Use copies of the preserved cold SQLite graph; never open or mutate the original via the
  current repository, and never touch canonical PostgreSQL.
- Preserve Mem0 extraction, support links, source contents, and admission state.
- Generate a new, identical retrieval embedding profile for both branches through OpenRouter
  (`qwen/qwen3-embedding-8b`). No Mem0 re-extraction or relationship recalibration.
- Generate tags once from each query and its existing tag catalog only, using
  `google/gemini-3.1-flash-lite`. No answers, evidence labels, original query tags, or retrieval
  scores are passed to the tagger. Cache all features before scoring.
- Six original training questions; five rounds; existing `query_evidence` learning policy with
  its unchanged defaults; only returned, benchmark-attributed evidence receives positive feedback.
- Twelve newly authored paraphrases (two per original question) never receive feedback. They
  are unseen wording, **not twelve independent tasks or unseen evidence**. Six additional
  same-history questions probe collateral ranking changes using manually identified evidence
  anchors. Those anchors need not exhaust every possible supporting turn.
- Primary endpoint: top-10 source-evidence MRR and macro recall on the twelve paraphrases,
  learned minus frozen. Report per-query gains/losses and every round, not only the final average.
- Controls: frozen vector-only rankings; frozen graph; disabled learning channels; same-history
  collateral target rank/hit. Audit the weight ledger and verify immutable evidence/vector/Mem0
  state matches across branches. Show any overlap between collateral anchors and training labels.
- Success requires final transfer MRR improvement without recall loss or collateral MRR/hit loss.
  Report intermediate regressions separately. No threshold or query changes after seeing scores.
- No generated-answer quality, causal discovery, cross-user transfer, multimodal learning,
  inference-attack privacy, or mature-graph claims are tested here.

The fresh retrieval embedding profile and fresh training tags mean absolute scores are not
directly comparable with the earlier Harrier/frozen-tag experiment. Only within-run comparisons
isolate the contribution of learning.

## Results

The first fixed-policy run completed successfully. The fixture was not changed after scoring.

| Twelve fresh paraphrases, top 10 | Frozen hybrid | After 30 feedback events |
|---|---:|---:|
| Evidence MRR | 0.606481 | 0.620370 |
| Macro evidence recall | 79.17% | 79.17% |
| Queries with any expected evidence | 10/12 | 10/12 |
| Direct-source MRR | 0.606481 | 0.620370 |

MRR increased by 0.013889, **2.29% relative**. Exactly one of twelve paraphrases improved in
MRR; eleven were unchanged. No query lost evidence recall. These are twelve wording variants
of six previously used histories, not twelve independent tasks. Six variants already had the
first relevant result at rank 1, and two variants still found no labeled evidence after learning.

The concrete gain was `projects-a`:

> Across my past and present work, how many projects have had me in charge?

The first answer-bearing source atom moved **rank 6 -> 5 -> 4 -> 3 -> 3 -> 3** across the cold
state and five feedback rounds. It describes the user's solo Data Mining class project using
customer purchase data (`atom_de0b5e867af05b400b864f65dce56465`). Its direct tag score was zero
before and after, its semantic score stayed fixed, and its relationship contribution rose from
approximately 0.272 to 0.546. The resulting score explanations include learned `co_used_with`
paths and a `related_tag=customer purchase data` contribution. This identifies an association-
mediated ranking gain, but does not isolate tag relations from co-use or normalization effects.
The evidence had been rewarded through the original training question; it is not newly discovered
or previously unseen evidence.

All six same-history collateral anchors retained their original ranks and remained in the
top 10: aggregate MRR 0.791667 and target hit rate 100% before and after. None of those anchors
was an original benchmark answer-bearing turn or explicitly selected for feedback. Lower-ranked
items changed for two collateral queries, so **do not claim their entire rankings were unchanged**.
The anchor labels do not enumerate every potentially relevant passage.

### Honest comparison with simpler retrieval

| Profile | Lineage-aware MRR | Lineage-aware recall | Direct-source MRR | Direct-source recall |
|---|---:|---:|---:|---:|
| Vectors only | 0.750000 | 68.75% | 0.506944 | 62.50% |
| Lexical + vectors, learning channels off | 0.494544 | 75.00% | 0.484127 | 72.92% |
| Frozen hybrid | 0.606481 | 79.17% | 0.606481 | 79.17% |
| Learned hybrid | 0.620370 | 79.17% | 0.620370 | 79.17% |

Lineage-aware scoring credits supporting source IDs attached to a retrieved derived item;
direct scoring credits only the returned source atom itself. The different results matter:
**the hybrid does not dominate vector-only retrieval on every metric**. Vector-only places its
first lineage-credited evidence earlier on average; hybrid covers more expected evidence and
has better exact-source ranking here. This is a retrieval/packing tradeoff, not measured answer
accuracy. The causal learning comparison is the final two rows, not the full difference from
vectors alone.

### Controls and cost

- Frozen branch, vector-only rankings, and lexical/vector rankings with learning channels off
  were unchanged after feedback.
- Source atoms, documents, vectors, non-co-use links (including Mem0), and calibration records
  retained identical hashes. All six namespace weight-ledger audits passed.
- The original cold SQLite snapshot checksum was unchanged. Canonical PostgreSQL and the Mac
  were not used or modified.
- 30 feedback events produced 720 weight transitions across 144 distinct edges. There were
  17 learned co-use edges. The 80 Mem0 typed proposals still had total active weight zero.
- API preparation embedded 280 atoms (184 source + 96 entity) and 24 queries, and generated
  query tags independently of training labels. Both branches reused the exact same features.
- 31 API calls: 19 embedding batches, 12 tag batches; six-worker concurrency.
- Provider-reported usage: 52,034 prompt tokens, 1,253 completion tokens; **$0.00451649** total.
  This is the cost of this incremental experiment, not the historical ingestion/extraction cost.
- Scoring and subsequent reproduction use no API calls.
- A second fresh-copy offline run reproduced every final ranking, score explanation, and metric
  exactly; all-round MRR/recall also matched. No individual query's measured MRR or recall fell
  below its cold value at any round. The repeated scoring run took approximately 50.6 seconds.
- Test run after adding the harness: **179 passed, 2 skipped**, plus 2 passing subtests. The two
  PostgreSQL tests were not configured for this laptop SQLite experiment; do not describe this
  as a new live-PostgreSQL acceptance run.

### Interpretation and shareable wording

The predeclared quality gate passed: final transfer MRR increased without aggregate transfer
recall or collateral target-quality loss. This is a small positive diagnostic, not statistical
validation, a tuned optimum, a standalone Mem0 comparison, or evidence for global learning.
No meaningful long-term, procedural, cross-user, or multimodal conclusions follow from it.

> We isolated our learning layer using identical frozen Mem0 data, evidence and embeddings.
> After 30 supervised feedback events, relevant evidence for one fresh query wording moved
> from rank 6 to rank 3. Across 12 new wordings, MRR rose from 0.606 to 0.620, with unchanged
> recall and no target-rank loss on six same-history control questions. The gain was small and
> concentrated in one case, but it came from our outcome-driven graph updates rather than new
> extraction or embeddings. This is early evidence of useful adaptation, not broad generalization.

### Reproduction

Run from the repository root:

```powershell
python scripts/run_learning_contribution.py --prepare-only
python scripts/run_learning_contribution.py --offline
python -m pytest tests/test_learning_contribution.py tests/test_mem0_experience.py tests/test_learning.py
```

Preparation uses the saved OpenRouter credential without printing or storing it in artifacts.
It sends only the selected public benchmark text/query/catalog data. API outputs are frozen
in ignored local storage; regenerating them may differ even with temperature zero. Offline runs
use fresh SQLite copies and retain detailed per-query reports, score explanations, round history,
configuration hashes, and runner hashes. No production retrieval/learning policy was changed.

- First report: `data/results/learning-contribution-v1/run-58e4mfny/report.json`.
- Reproduction report: `data/results/learning-contribution-v1/run-lpy21agk/report.json` (includes
  direct recall, explicit feedback-overlap checks, and every-round per-query regression checks).
- Frozen features: `data/results/learning-contribution-v1/features.json`.
- Prepared snapshot: `data/results/learning-contribution-v1/prepared.sqlite3`.
- Fixture SHA-256: `513e56a2a0b92cf354dcce4ea7419c23763766ff9add43e14ea76ec185f72ba7`.
- Original snapshot SHA-256: `a720239a1eea35dc66f7263dc8ccbdea991336493ad7ba0de81a3bd3ee2e5b7d`.

If preparation is interrupted before publishing the feature cache, use a fresh output directory;
the harness refuses to overwrite a partially prepared database. Do not rerun API preparation
merely to obtain a more favorable feature set.

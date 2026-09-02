# Repository Feature Adapter v1

Status: implemented in shadow/read-only mode  
Implementation: `src/data_retrieval/collective/repository_features.py`

## Decision

Connect the deterministic collective feature extractor to the existing `Repository` protocol,
but keep it outside ingestion, retrieval scoring, and weight mutation. This lets us inspect real
evidence and calibrate the gate before it can affect users.

The realistic alternatives were to wire it directly into learning now, or to wait for the
future global database schema. Direct wiring would make uncalibrated thresholds operational.
Waiting would leave the formulas tested only on hand-authored evidence. The read-only adapter
gives real measurements while preserving rollback: deleting the report changes nothing.

## Data flow

```text
stored TagRelation + WeightEvent ──> local ledger evidence
cached AtomEmbedding ──────────────> similarity margin + duplicates
native Mem0 atom links ────────────> deduplicated support/conflict lineage
native Temporal atom links ────────> current/superseded/conflict ratios
copied outgoing relation weights ──> in-memory impact perturbation
                                      │
                                      v
                          CollectiveFeatureTriage
                                      │
                                      v
                         payload-free JSON report
```

The adapter maps namespace-local canonical tag text to the experimental stable shared concept
ID function. This is not yet the production global concept registry.

## Safety boundary

The adapter calls only repository reads. It does not:

- generate or persist embeddings;
- call Ollama, OpenRouter, Mem0, or Temporal History;
- add calibration or feedback events;
- change serving weights, atoms, links, tags, or snapshots;
- export atom text, raw vectors, Mem0 lineage IDs, or contributor buckets in its report.

SQLite/PostgreSQL repository construction still performs ordinary schema initialization and may
migrate an old database before observation. Run against a compatible database and keep normal
backups; use a copy when the source must remain byte-unchanged. After repository construction,
the feature adapter itself is read-only.

## Evidence semantics

Local feedback events are combined into one explicitly unattributed bucket. Feedback IDs prove
distinct events, not distinct people. Consequently `contributor_independence_known` is false,
independent-contributor maturity is zero, and the report will conservatively escalate many local
relationships. That is a schema finding, not a defect to hide with guessed identities.

Cached vectors are read only when both provider and exact model/profile identity are supplied.
Stale vectors whose content hash no longer matches their atom are ignored. Missing models remain
missing rather than triggering inference.

Mem0 support and conflict facts are collapsed to their native source atoms before counting, so
multiple derived copies do not create false corroboration. Temporal `SUPERSEDES` direction is
the same as retrieval: the link points from current/replacement evidence to older evidence.

The cheap impact estimate copies one source tag's outgoing weights, adds the versioned `0.05`
candidate increment only to that copy, normalizes relative shares, and measures target and
unrelated changes. A later frozen-query replay must test whether this proxy predicts actual
retrieval movement.

## Command

Without vectors:

```powershell
python -m data_retrieval observe-repository-features `
  --db .\data.sqlite3 `
  --namespace personal `
  --limit 100
```

With already-cached Ollama embeddings:

```powershell
python -m data_retrieval observe-repository-features `
  --postgres-dsn $env:DATA_RETRIEVAL_POSTGRES_DSN `
  --namespace personal `
  --embedding-provider ollama `
  --embedding-model $env:OLLAMA_EMBEDDING_MODEL `
  --limit 100 `
  --report .\data\results\repository-features-v1.json
```

For a profiled embedding, use the stored model identity (for example
`model-name::harrier-retrieval-v1`), not merely the underlying Ollama model name.

The report contains stored/eligible/skipped/truncated relation inventory, disposition/reason/
missing-source counts, min/median/p95/max feature distributions, candidate latency,
payload-free per-candidate explanations, and explicit limitations.

## First repository observations (2026-09-03)

Two copied SQLite databases were observed; source databases were left untouched.

The Mem0 smoke repository exposed one eligible relation. It was routed to asynchronous review
in under 1 ms of candidate processing because cached vectors and Temporal evidence were absent,
contributor independence was unknown, and alignment remained uncertain. Native Mem0 lineage
was detected and supplied bounded interpretation support.

The raw legacy project-history acceptance file contained 45 relation rows. Normal repository
initialization on its copy migrated that old state to 21 current canonical relations; that was
existing migration behavior, not feature-adapter filtering. The observer scored all 21 and
reported zero skipped relations. They completed in about 35 ms total (about 1.6 ms mean): 13
asynchronous reviews and 8 holds. All lacked cached vectors and Mem0 evidence, so none was
eligible for the cheap path. Temporal evidence produced conflict values from `0` to `0.333`; the
local relative impact proxy ranged from `0` to `1`.

These runs validate storage wiring, bounded degradation, diagnostics, and latency on existing
data. They do not calibrate quality: the project-history fixture is not a public development set,
and its missing processors plus unknown contributor attribution force conservative decisions.

Artifacts: `data/results/repository-features-v1.json` and
`data/results/repository-features-project-history-v1.json` (local ignored result files).

## Next gate

Run a bounded public LongMemEval namespace observation, inspect threshold cases, and record what
percentage is cheap, asynchronous review, or held. Do not tune on the final benchmark. The first
required architecture repair exposed by this adapter is a privacy-safe contributor-attribution
field in collective observation events; local feedback IDs must never fill that role.

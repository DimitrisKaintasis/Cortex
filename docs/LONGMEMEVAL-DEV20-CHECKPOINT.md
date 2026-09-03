# LongMemEval Dev20 checkpoint

Updated: 2026-09-03  
Status: ingestion, enrichment, recovery, and 20-case retrieval evaluation complete

## Scope and paths

- Selection: `evals/longmemeval_dev20_v1.json`
- Source: `data/benchmarks/longmemeval/longmemeval_oracle.json`
- Canonical database: `artifacts/longmemeval-dev20-clean-v1.sqlite3`
- Original local Temporal state prefix: `artifacts/longmemeval-dev20-temporal.sqlite3`
- API recovery Temporal state prefix: `artifacts/longmemeval-dev20-temporal-api.sqlite3`
- Final report: `data/results/longmemeval-dev20-pipeline-v1.json`
- Mem0 config: `evals/longmemeval_dev20_mem0_config.json`

The diagnostic databases `artifacts/longmemeval-dev20-v1.sqlite3` and
`artifacts/longmemeval-dev20-gemma-smoke.sqlite3` are not canonical results.

## Execution history and model provenance

The first 17 namespaces were enriched locally through the Mac:

- Tags: `gemma4:e2b-mlx`
- Temporal: `qwen3.5:4b`
- Retrieval embeddings: Harrier 0.6B F16 with `harrier-retrieval-v1`
- Maximum local enrichment workers: 2

The local run stopped after one malformed Temporal response. The three incomplete namespaces
(`6a1eabeb`, `6d550036`, and `75832dbd`) were recovered through OpenRouter with
`google/gemini-3.1-flash-lite`. Previously committed local proposals were retained rather than
overwritten. Harrier on the Mac still produced their stored embeddings.

The final evaluation used Gemini 3.1 Flash Lite consistently for query-tag proposals across all
20 cases and Harrier for query embeddings. It ran in `--evaluation-only` mode, so no persisted
document enrichment was regenerated. This is therefore a valid end-to-end pipeline smoke and
retrieval test, but it is a mixed-enrichment run and must not be presented as a controlled
single-model benchmark.

## Reliability changes proven during the run

- Ollama Temporal summaries retry malformed schema output with corrective feedback.
- OpenRouter tag batches retry and split when a provider rejects a large request.
- OpenRouter retries successful HTTP responses that contain malformed model JSON.
- `run-longmemeval --evaluation-only` separates query understanding from stored-document
  enrichment, allowing repeatable retrieval evaluation without expensive reprocessing.
- All focused tests, the full suite, and Ruff passed after these changes.

## Final stored snapshot

- 20 isolated namespaces
- 59 total source and generated documents
- 1,662 canonical tags
- 5,309 tag relations
- 590 cached retrieval embeddings
- 13,652 immutable weight events
- Tag candidates: 1,862 canonicalized, 1,005 merged, 3 rejected, 0 left proposed

## Final top-10 retrieval results

- Evaluated cases: 20/20
- Session hit rate: 100%
- Session recall: 98.75%
- Turn hit rate: 100%
- Turn recall with Temporal lineage credit: 96.25%
- Mean reciprocal rank with lineage credit: 1.0
- Direct raw-atom session recall: 96.25%
- Direct raw-atom turn recall: 87.42%
- Direct raw-atom mean reciprocal rank: 0.8583
- Mean retrieval latency: 1,585 ms

Temporal lineage contributed most clearly to multi-session evidence coverage: multi-session turn
recall was 81.25% with lineage credit versus 45.42% for exact retrieved raw atoms only. This is
promising evidence that summaries can recover broader source coverage, but the 20-case slice is
too small and was not an ablation against an otherwise identical no-Temporal system.

## Required benchmark-only catalog step

LongMemEval namespaces begin with an empty canonical tag catalog. Normal ingestion correctly
quarantines model proposals. The explicit `--resolve-benchmark-tags` option groups exact
normalized proposal families, promotes the strongest family member when confidence is at least
`0.65`, merges exact duplicates, and rejects weaker families. It is off by default and does not
weaken production quarantine.

## Next step

Bootstrap Mem0 for the `longmemeval-dev20-v1:` namespace prefix, then observe repository features
across all selected namespaces and aggregate the cheap-review, async-review, and hold outcomes.
Do not tune production thresholds from this small mixed-provider slice alone.

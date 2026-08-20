# Tag proposal model benchmark

- Date: 2026-08-19
- Mac: Apple M4, 16 GB unified memory
- Ollama: 0.32.3
- Models: `gemma4:e2b-mlx` and `gemma4:12b-mlx`
- Generation: temperature 0, thinking disabled, maximum 6 tags

## Purpose

This is a small engineering benchmark for atom-level tag proposal, not a general model
leaderboard. It checks structured-output validity, reuse of an existing tag catalog,
coverage of expected retrieval concepts, resistance to an instruction embedded inside an
atom, multilingual catalog reuse, and latency.

The six synthetic cases cover:

1. the laptop/Mac deployment boundary;
2. the SQLite backup decision;
3. Temporal History summary lineage;
4. SSH port forwarding;
5. an instruction-injection attempt inside an atom; and
6. Greek text with an English tag catalog.

The benchmark is reproducible with:

```powershell
python .\scripts\benchmark_tag_models.py gemma4:e2b-mlx gemma4:12b-mlx
```

The Ollama SSH tunnel must already be open at `http://127.0.0.1:11435`.

## Results

The second identical run produced the following stable quality scores and representative
latencies. The first request for each model includes model-switch/loading overhead.

| Metric | Gemma 4 E2B MLX | Gemma 4 12B MLX |
| --- | ---: | ---: |
| Valid structured responses | 6/6 | 6/6 |
| Expected concept recall | 76.2% | 81.0% |
| Output tags reused from catalog | 91.3% | 100% |
| Unrelated/injected forbidden tags | 0 | 0 |
| First request after model switch | 4.52 s | 7.64 s |
| Median request latency | 0.66 s | 2.64 s |
| Six-case total | 7.54 s | 20.41 s |

Expected concept recall is deliberately coarse. Tags represent what an atom is about, so
a negated or deferred concept may still be useful for retrieval. Human inspection of the
actual tag sets remains more important than the aggregate number.

## Quality observations

- Both models ignored the embedded instruction to tag a graph atom as `cooking`.
- E2B was about four times faster on warm requests.
- E2B reduced the graph/injection case to only `weighted graph`; 12B also retained
  `atom relationships` and `retrieval`.
- For Greek input, E2B created generic `storage` and `inference` tags instead of reusing
  the catalog's `canonical storage` and `remote inference`. The 12B model reused the exact
  catalog tags.
- E2B tended to tag more mentioned contrast/future concepts. The 12B model selected a
  smaller, more central set. Either behavior can be useful, but the 12B output is currently
  less likely to fragment the tag catalog.

## Decision

Keep `gemma4:12b-mlx` as the default ingestion tag proposer for now. Its warm median of
about 2.6 seconds is acceptable after disabling reasoning, and its improved specificity
and exact catalog reuse matter more than the remaining latency difference at the current
scale.

Keep `gemma4:e2b-mlx` installed as an explicit fast experimental option. It is promising
for high-throughput first-pass tagging and for the project's future research into helping
small models with retrieved structure. Do not add automatic model routing yet; one default
model keeps ingestion behavior easier to understand and evaluate.

Thinking must remain disabled for this classification call. With thinking enabled, both
models were much slower and intermittently failed to put valid JSON in the final content
field. Setting Ollama's `think` request field to `false` made both models produce 6/6 valid
responses.

## Mem0 extraction contract smoke test

On 2026-08-20, the same Gemma models were tested through Mem0 OSS 1.0.1 rather than the
project's purpose-built tag prompt. This is a different interface: Mem0 owns the extraction
prompt and JSON parser, so our tag proposer's `think=false` and schema handling do not apply.

| Model | Mem0 result | Approximate one-batch latency |
| --- | --- | ---: |
| `gemma4:e2b-mlx` | malformed extraction JSON; zero memories | 24 s |
| `gemma4:12b-mlx` | malformed extraction JSON; zero memories | over 3 min |
| `qwen3.5:4b` | valid; two distilled memories with native lineage | 70 s cold |

`qwen3.5:4b` also returned valid JSON in a direct Ollama format-constrained check and occupies
about 3.4 GB on the Mac. It is therefore the current Mem0 extraction model, while
`gemma4:12b-mlx` remains the tag-proposal default. This is only a compatibility smoke test;
throughput, warm latency, and memory quality still require a representative multi-session
benchmark before a large backfill.

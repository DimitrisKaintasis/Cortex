# Embedding model benchmark

- Date: 2026-08-20
- Runtime: remote Mac Mini through a local SSH tunnel to Ollama 0.32.3
- Corpus: `evals/retrieval_cases.json`, nine top-1 tag, semantic, multilingual, code,
  and temporal cases

## Decision

Use Microsoft Harrier OSS v1 0.6B with the `harrier-retrieval-v1` preprocessing profile as
the current quality default.

The upstream model is MIT-licensed, supports 94 languages, produces 1024-dimensional
vectors, accepts up to 32K tokens, and reports 69.0 on Multilingual MTEB v2. Harrier
requires asymmetric inputs: stored documents are embedded plainly, while queries are
prefixed with a one-sentence retrieval instruction. The profile is included in the
stored model identity so vectors created with incompatible preprocessing are never
reused.

Sources: [official Microsoft model card](https://huggingface.co/microsoft/harrier-oss-v1-0.6b)
and [current MTEB leaderboard](https://huggingface.co/spaces/mteb/leaderboard).

## Installed artifact

Ollama does not currently publish Harrier in its official library. The Mac therefore
uses this community F16 GGUF conversion of the official Microsoft model:

```text
hf.co/mradermacher/harrier-oss-v1-0.6b-GGUF:F16
Ollama model ID: b04ed69796b0
Stored size: 1.2 GB
```

F16 was selected over Q4 to avoid an unmeasured embedding-quality loss. The upstream
Microsoft weights remain the source of truth. For production distribution, reproduce
and verify the GGUF conversion from the official checkpoint rather than depending on a
mutable community tag.

## Results

| Configuration | Hit@1 | MRR | Forbidden temporal results | Elapsed |
| --- | ---: | ---: | ---: | ---: |
| No embedding model | 55.6% | 0.556 | 0% | not compared |
| Qwen3 Embedding 0.6B | 100% | 1.000 | 0% | 3.01 s |
| Harrier OSS v1 0.6B F16 | 100% | 1.000 | 0% | 2.37 s |

Elapsed time covers the full command on a small warmed corpus and is not a throughput
benchmark. Both models saturated the current quality cases. Harrier becomes the default
because it did not regress local behavior, was faster in this run, and its official
upstream benchmark is stronger. Qwen remains installed as a fallback and comparison
model.

## Next evaluation work

- Add every real retrieval miss as a labeled hard-negative case.
- Add longer conversations and documents instead of only short atoms.
- Expand Greek/Romanian cross-language queries and code-search cases.
- Measure cold-start latency, atoms per second, and peak memory on the shared Mac.
- Reconsider 270M Harrier or Granite 311M R2 if sustained ingestion throughput or vector
  storage becomes a constraint.

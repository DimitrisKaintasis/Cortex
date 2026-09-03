# Mem0 entity/provenance real-model smoke result

Date: 2026-09-03
Status: structural gate passed; cheap-model promotion gate failed

## Setup

- Mem0: 1.0.1, normal `Memory.add(infer=True)` behavior
- LLM: `gemma4:e2b-mlx` on Mac Ollama through the SSH tunnel
- Embedder: `qwen3-embedding:0.6b`, explicitly configured for 1024 dimensions
- Mem0 graph: embedded Kuzu on the laptop
- Cortex store: dedicated SQLite smoke database
- Canonical production target remains PostgreSQL

## Compatibility findings

Mem0 1.0.1's Ollama adapter did not forward tool schemas and always returned an empty tool-call
list. An instance-local transport shim now forwards Mem0's existing tools, disables hidden
thinking for structured calls, and normalizes Ollama's native tool-call response. No Mem0 prompt,
entity algorithm, graph-update algorithm, or installed package file was changed.

`qwen3.5:4b` then extracted five entities correctly but returned entity names where the
relationship tool required relationship objects. Because the missing predicates could not be
recovered without invention, this model failed the relationship gate.

`gemma4:e2b-mlx` followed the ordinary Mem0 relationship schema. Requiring three separate
endpoint-provenance arrays degraded its output and caused self-links, so the contract was reduced
to the originally planned single `evidence_source_ids` array per relationship. Both private
entity endpoints link to that bounded relationship evidence set.

## End-to-end results

The final two-atom smoke run produced:

| Metric | Result |
|---|---:|
| Normal Mem0 memories returned | 2 |
| Private entity atoms imported | 5 |
| Valid entity relationships imported | 3 |
| Relationships quarantined | 0 |
| Exact entity-to-source support links | 6 |
| Entity tags copied | 0 |
| Replay-protection signals | 11 |

Observed relationships:

| Source | Predicate | Target | Attributed evidence |
|---|---|---|---|
| Alice | leads | Project Helios | atom 1 |
| OpenAI | provides | inference service | atom 1 |
| Mac Mini | runs_evaluation_jobs_on | Project Helios | atom 2 |

The first two are correct. The third has the correct endpoints and general meaning, but `on` is a
weaker rendering than the source's `for`. This run exposed a chunker bug: it selected the latest
available whitespace instead of honoring paragraph-before-sentence-before-space priority, which
split `Mac Mini` across atoms.

After correcting the deterministic boundary priority, a fresh run kept `Mac Mini` together. Its
entity linked only to the correct second evidence atom, the other two relationships linked only to
the first atom, and the model improved the predicate to `runs_jobs_for`. Replay again made zero
model calls and zero writes.

A second identical bootstrap resumed the completed batch with zero model calls and zero new
links. Persisted entity atoms had zero tags. A tags-disabled query for Project Helios surfaced
the canonical source atom with a non-zero `mem0_entity_path` score, confirming serving retrieval
does not depend on Mem0 or Kuzu after import.

## Labeled quality gate

The checked-in `mem0-entity-quality-v1` fixture contains ten short cases and twelve expected
relationships. The final `gemma4:e2b-mlx` run completed in 48.6 seconds with:

| Metric | Result |
|---|---:|
| Entity precision / recall | 91.3% / 100% |
| Directed endpoint precision / recall | 71.4% / 83.3% |
| Direction accuracy among comparable endpoints | 100% |
| Predicate fidelity on correct directed endpoints | 90% |
| Evidence precision / recall on correct relationships | 100% / 100% |
| Provider errors / quarantined relationships | 0 / 0 |
| Mean latency per case | 4.86 seconds |

The cheap model produced all required entities and copied exact evidence IDs reliably, but it
added weak extra edges and missed two expected directed edges in the cross-atom pronoun case. It
therefore failed the 90% endpoint precision/recall promotion gate.

`gemma4:12b-mlx` was tested only on the cheap model's failed or ambiguous cases. All four calls
failed with Mac MLX compute errors, including an explicit Metal out-of-memory error. A clean
single-case retry with no resident Ollama models also failed after about 21 seconds. The model is
not a viable local escalation tier on this Mac.

## Decision

The integration is structurally ready: transport, graph persistence, provenance validation,
quarantine, replay, and retrieval traversal work end to end. It is not approved for unchecked
large ingestion because the cheap model failed semantic endpoint precision and recall.

Use `gemma4:e2b-mlx` only as a cheap proposal generator for now. Keep malformed outputs
quarantined. The next experiment should evaluate a remote/API reviewer or a constrained second
review pass only on cases flagged by cheap structural and consistency features; the local 12B
model must not be selected as a fallback on this hardware.

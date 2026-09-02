# Isolated capability gates

The first architecture test suite is deliberately deterministic and fast. It proves that
each subsystem obeys its contract before pairwise integrations or full-system benchmarks
can hide a fault behind a single aggregate score.

Run it with:

```powershell
python -m data_retrieval evaluate-capabilities
```

The command reads `evals/isolated_capabilities_v1.json` and writes the full report to
`data/results/isolated-capabilities.json`. Both paths can be overridden with `--fixture`
and `--report`.

## What is gated

| Gate | Owner | Contract checked |
| --- | --- | --- |
| Canonical core | Data Retrieval | Stable identity, replay, SQLite persistence, role, modality, and hydration |
| Mem0 boundary | Mem0 adapter | Derived facts, exact source support, batch/support separation, and resumability |
| Temporal projection | Temporal History adapter | Calendar hierarchy, derived role, source lineage, and versioned metadata |
| Tags | Tags | Canonicalization, duplicate collapse, atom attachment, and replay |
| Outcome learning | Data Retrieval / Tags | Selected-only attribution, bounded weight change, no collateral update, and replay rejection |
| Retrieval channels | Data Retrieval | Independently functioning lexical, semantic, and tag channels, plus generated-query-tag canonicalization |
| Evidence packing | Data Retrieval | Source quota, derived cap, bounded output, and explicit underfill |

Every report includes the fixture path and content hash, Git revision and dirty state, Python
version, storage adapters, fixed processor profiles, feature switches, random seed, timings,
and artifact location. A fixture with one deliberately wrong expectation fails only the owning
gate, which keeps diagnosis local. The command also returns a nonzero process exit code when
any gate fails, so it can be used as a CI gate.

## What a pass does not prove

This suite uses fixed Mem0 outputs, mock Temporal summaries, fixed tag proposals, and a tiny
deterministic embedder. A pass proves interfaces and invariants; it does **not** prove real-model
precision, recall, summary fidelity, latency, cost, held-out learning improvement, or useful
behavior at scale.

Those claims need short quality fixtures next. The order is:

1. add real-model quality cases inside the Mem0, Temporal, and Tags gates;
2. measure retrieval and learning on held-out examples;
3. run pairwise integrations with matched inputs and budgets;
4. allow full composition only after the owning gates pass.

This distinction prevents a mock-backed contract test from being reported as evidence that a
model or the combined architecture is effective.

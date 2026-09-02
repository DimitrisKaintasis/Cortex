# Collective Feature Extractor v1

Status: deterministic shadow contract and read-only repository adapter implemented
Implementation: `src/data_retrieval/collective/features.py`,
`src/data_retrieval/collective/repository_features.py`
Tests: `tests/test_collective_features.py`, `tests/test_repository_features.py`

## Purpose

The selective review cascade needs bounded values for uncertainty, impact, risk, conflict,
contributor concentration, rarity, alignment, novelty, and sensitivity. The v1 extractor derives
them from measurable graph state and cached ingestion projections without making a new model
call.

It accepts small aggregate evidence records rather than a repository. This keeps the formulas
pure and testable across memory, SQLite, and PostgreSQL. Storage-specific adapters remain future
work and must produce the same input contract.

```text
ledger aggregates ─┐
cached vectors ─────┤
Mem0 lineage ───────┼─> CollectiveFeatureExtractor
Temporal state ─────┤             │
shadow dry-run ─────┘             ▼
                           CheapReviewFeatures
                                  │
                                  ▼
                           CheapReviewTriage
```

The output contains component values, provider/profile provenance, and explanations. It does not
contain contributor buckets, Mem0 source-lineage IDs, raw embeddings, source atoms, or payloads.

## Deterministic calculations

### Ledger

```text
positive = sum contributor positive support
negative = sum contributor negative support
total = positive + negative

support_maturity = min(1, total / 5)
contributor_maturity = min(1, independently attributable buckets / 5)
relationship_maturity = min(support_maturity, contributor_maturity)

conflict_balance = 2 * min(positive, negative) / total
ledger_conflict = conflict_balance * support_maturity

contributor_concentration = largest contributor support / total
concept_rarity = 1 - min(1, concept contributors / 10)
```

The constants are versioned experiment defaults, not production values.

If a repository exposes feedback volume but cannot prove independent contributors, the adapter
sets `contributor_independence_known=false`. The volume remains visible for conflict analysis,
but contributor maturity is forced to zero. A local feedback ID is not treated as a person.

### Vectors

Only cached similarities are accepted; the extractor does not receive a raw vector. Similarity
below `0.65` gives zero alignment. The range `0.65..0.85` is scaled, and the result is multiplied
by an ambiguity factor derived from the gap between the best and second-best match:

```text
similarity_position = clamp((best - 0.65) / (0.85 - 0.65), 0, 1)
ambiguity_factor = clamp((best - second) / 0.15, 0, 1)
vector_alignment = similarity_position * ambiguity_factor
vector_novelty = 1 - best
```

An approved catalog mapping takes precedence. If vectors are present but ambiguous, Mem0 support
cannot conceal that ambiguity. Vector novelty contributes to uncertainty in proportion to the
candidate update's impact. Near-duplicate fraction contributes to risk.

These similarity constants require calibration for each embedding provider/model/profile.

### Mem0

Supporting and conflicting derived facts are deduplicated by source lineage before measurement.
Three copies of one Mem0 fact supported by one atom remain one lineage.

```text
unique_lineages = unique(supporting union conflicting)
balance = 2 * min(support_count, conflict_count) / derived_fact_count
Mem0 maturity = min(1, unique_lineages / 3)
Mem0 conflict = balance * Mem0 maturity
```

An explicit update signal creates at least `0.5` Mem0 conflict. When vectors are unavailable and
there is no approved mapping, three non-conflicting supporting lineages may provide at most
`0.75` interpretation confidence. Mem0 never increases relationship maturity or independent
contributor count.

### Temporal

```text
temporal_uncertainty = max(
    superseded_ratio,
    temporal_conflict_ratio,
    1 - current_support_ratio
)
```

Temporal uncertainty also contributes to conflict and risk. This prevents semantically similar
but superseded evidence from being treated as current agreement.

### Shadow impact

The caller supplies measurements from an ephemeral candidate snapshot:

```text
influence_impact = abs(candidate - active) / 0.30
regression_risk = unrelated_max_delta / 0.10
rank_impact = abs(target_rank_change) / 5
expected_impact = clamp(max(all three), 0, 1)
```

If no dry-run exists, impact defaults conservatively to `0.50` and is marked missing.

The repository adapter supplies a first cheap approximation: it adds `0.05` to the selected tag
relation only in memory, normalizes all outgoing weights from the source tag into relative
shares, then measures the selected share/rank change and the largest unrelated share change.
This is useful for triage but is not a full replay of frozen retrieval queries.

### Final combination

```text
uncertainty = max(
    1 - alignment confidence,
    ledger conflict,
    insufficient relationship evidence,
    Mem0 conflict,
    temporal uncertainty,
    impact-weighted vector novelty,
    maturity-scaled missing-processor penalty
)

risk = max(
    sensitivity,
    scope violation,
    abuse anomaly,
    concept rarity,
    contributor concentration,
    vector duplicate fraction,
    unrelated regression,
    temporal uncertainty
)
```

Taking the maximum is intentionally conservative and explainable. A future learned gate may be
tested in shadow mode, but it must beat this deterministic baseline on a frozen evaluation set.

## Contract results

The deterministic tests verify:

- mature, consistent, fully supported evidence uses the cheap path;
- immature evidence with missing processors is held for review;
- a clear cached vector match supports alignment;
- a small gap between the best two vector matches triggers review;
- duplicate Mem0 facts with one lineage count once;
- Mem0 corroboration is bounded below full confidence;
- Mem0 conflict escalates but cannot create contributors;
- Temporal supersession holds a high-impact update;
- unavailable processors degrade gracefully and do not penalize a mature ledger relationship;
- processor agreement cannot manufacture maturity for a cold relationship;
- sensitivity is a hard hold;
- exported features contain neither contributor nor lineage identifiers.

## Repository connection

`ShadowRepositoryEvidenceAdapter` now reads the same repository protocol used by memory,
SQLite, and PostgreSQL. It discovers existing tag relations, bounds atoms per side, validates
cached embedding hashes, follows Mem0 `SUPPORTED_BY` and `CONFLICTS_WITH` links, follows
Temporal `SUPERSEDES` and summary links, and generates a payload-free namespace report. It
does not call repository mutation methods or model providers.

See `REPOSITORY-FEATURE-ADAPTER.md` for its exact boundary and command.

## Remaining work

1. Add privacy-safe collective contributor buckets to the future observation-event schema.
2. Calibrate vector similarity and triage thresholds on a fixed development set.
3. Add a full frozen-query impact replay alongside the cheap relative-weight perturbation.
4. Run the extractor on real public benchmark entries and inspect explanations.
5. Connect only escalated public cases to a real local and strong reviewer comparison.

Until those adapters exist, the extractor is an executable policy contract rather than a live
ingestion dependency.

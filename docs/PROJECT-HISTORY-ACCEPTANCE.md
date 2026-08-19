# Project-history acceptance test

Date: 2026-08-20

This acceptance test asks whether the current pipeline preserves the original Tags idea
while adding Temporal History as a derived layer. It uses a small, synthetic history of
the project's actual architectural decisions rather than unrelated example text.

## Result

The live Mac-backed run passed:

- 10 timestamped source events ingested into laptop SQLite;
- optional AI tags added to 2 representative events;
- 10 source atoms and 18 Temporal summary atoms embedded with Harrier;
- 18 Temporal summaries generated: 8 six-hour, 7 day, 1 week, 1 month, and 1 year;
- 50 direct source-lineage links and 23 summary-to-summary lineage links persisted;
- 8 of 8 retrieval checks passed;
- one positive retrieval outcome created 1 learned atom-to-atom `CO_USED` link, updated
  2 atom-tag weights, and updated 45 tag-to-tag relationships.

The isolated outputs occupied approximately 639 KB for the canonical/retrieval SQLite
database and 74 KB for Temporal History's rebuildable state database.

## What the checks establish

The original model remains intact: atoms are independently retrievable, explicit tags
participate in ranking, and weights change only after explicit useful-outcome feedback.
The temporal integration does not replace those structures. It adds summary atoms with
auditable lineage and a temporal lens over the same evidence.

The test also establishes that:

- `current_state` retrieves the separate ordered-step add-on and excludes the older
  unordered-atoms source;
- `as_of` retrieves the older unordered-atoms state before that update;
- `current_state` retrieves Harrier and excludes the superseded Qwen source;
- `as_of` retrieves Qwen before Harrier was selected;
- `history` retrieves both embedding-model decisions;
- a broad weekly architecture query surfaces Temporal summary atoms.

The two state replacements are declared explicitly in the fixture as `SUPERSEDES`
relationships. The pipeline interprets those links; it does not yet infer supersession
automatically from arbitrary prose.

## Quality finding

The broader week, month, and year summaries can contain nearly identical text when they
cover the same small dataset. Also, a `current_state` result correctly ranks the newest
source first and removes the superseded source, but it may still include an older
period-summary atom as lower-ranked `continuity` context.

That is acceptable for evidence retrieval, but an answer-generation layer must not
present such continuity as current truth. Before relying on generated answers, add
context packing that deduplicates near-identical summaries and clearly labels historical
continuity versus current-state evidence.

## Reproduce

With a secure local tunnel to Ollama already running, use fresh output paths:

```powershell
python scripts/run_project_history_smoke.py `
  --database .local-tests/project-history-smoke.sqlite3 `
  --temporal-state .local-tests/project-history-smoke-state.sqlite3
```

The runner refuses to overwrite either output. This prevents an acceptance test from
silently mutating an earlier result or a real project database. The fixture is
[`evals/project_history_smoke.json`](../evals/project_history_smoke.json).

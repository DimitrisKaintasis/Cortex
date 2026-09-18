# Architecture Intent Recovery and Agent Handoff

> **Historical source — August 20, 2026.** This document preserves the architecture context
> packet used during the Data Retrieval reconciliation. It is not the current operational or
> architectural authority. For current boundaries and status, read
> [Architecture](../ARCHITECTURE.md),
> [Architecture reconciliation](../ARCHITECTURE-RECONCILIATION.md), and the
> [Execution roadmap](../EXECUTION-ROADMAP.md).

This handoff was originally prepared for the agent building `DimitrisKaintasis/Data-Retrieval`.
It recovers intent from the original Tags Project, DebUI and Cortex work, Temporal History, and
the Data Retrieval repository as they existed on August 20, 2026. Paths, implementation status,
and instructions below are retained as historical evidence and may no longer describe the
current branch.

> Primary instruction to the agent
> Do not treat the current codebase as the complete specification of the system. The project is a selective migration of several generations of design work. Your job is to understand the preserved intent first, then propose implementation changes. Deferred ideas are not rejected ideas. Several of the most important long-term capabilities — emergent groups, procedural/skill distillation, generalized payload handling, and semantic-temporal traversal — are intentionally not fully implemented yet.

## 1. Executive directive

The Data-Retrieval project is not intended to become a conventional vector database with a few extra metadata fields. Its long-term purpose is to be a payload-agnostic, associative memory and capability graph: arbitrary pieces of information are represented as addressable atoms; tags and learned relationships provide semantic routing; Temporal History supplies auditable chronology; repeated successful use can produce higher-order groups and procedures; and specialized add-ons decide how retrieved atom types are consumed by applications such as AI agents.

The current repository deliberately implements this in stages. That staging is important: some old ideas were simplified, some were replaced by safer versions, and some were explicitly deferred until retrieval quality could be measured. Do not infer that a missing runtime feature was abandoned merely because it is not present in the current domain model.

> Core mental model
> Semantic structure decides what memory is relevant. Temporal structure explains when it was true and how it changed. Learned relationships capture what repeatedly proves useful together. Emergent groups compress recurring structure. Procedural/skill atoms externalize reusable problem-solving steps. Add-ons turn retrieved atom types into application-specific behavior.

## 2. Non-negotiable architecture invariants

- Atoms are the canonical addressable units of stored information. The long-term abstraction is payload-agnostic even though the current Python Atom model is still text-centric.

- Raw/source evidence is preserved. Learning, summarization, refinement, grouping, or skill synthesis must be additive and traceable; they must not silently rewrite or delete source truth.

- Factual/provenance relationships and learned/behavioral relationships are different meanings and must remain separable.

- The tag/concept graph is the primary semantic address space. Embeddings are a replaceable signal, not the entire memory architecture.

- Temporal History is a derived projection over canonical atoms, not a competing canonical database.

- Time should normally be applied after or alongside semantic relevance, not as a global recency penalty that distorts timeless queries.

- Returned results must not automatically reinforce themselves. Learning requires attributable feedback, outcomes, teacher/calibration evidence, or other explicit signals.

- Group formation and workflow/skill synthesis should be evidence-gated. Repeated co-occurrence alone is not enough to promote a stable abstraction.

- Ordered procedures are a separate structure from associative graph links. CO_USED is not PRECEDES; semantic relatedness is not procedural order.

- The graph is a logical architecture, not a requirement to use Neo4j. PostgreSQL/SQLite may remain canonical if they meet measured traversal needs.

- External systems such as Mem0 and Temporal History are providers/projections. Data-Retrieval must remain usable when they are unavailable.

## 3. Status vocabulary used in this handoff

| Status | Meaning |
| --- | --- |
| Implemented | Present in the current Data-Retrieval runtime or documented acceptance path. |
| Accepted / deferred | Explicitly preserved by current architecture documents but intentionally postponed until evaluation justifies the complexity. |
| Recovered historical intent | Present in older Tags/DebUI/Cortex design and still relevant to the project direction, but not necessarily accepted verbatim. |
| Clarified today | A design interpretation agreed in the current architecture discussion; the agent should preserve it in planning and, where appropriate, record it in a new ADR/ledger update before implementation. |

## 4. Mandatory reading order

Read these in order. Do not jump directly into source files and infer the architecture only from what happens to be implemented today.

| Order | Read | Why |
| --- | --- | --- |
| 1 | Data-Retrieval/README.md | Current operational picture: implemented features, commands, retrieval stages, temporal bridge, embeddings, feedback, storage, evaluation. |
| 2 | Data-Retrieval/docs/CONTEXT_LEDGER.md | Most important intent-recovery document. Separates invariants, implemented foundation, accepted next layers, intentionally separate future procedure structure, and rejected/superseded ideas. |
| 3 | Data-Retrieval/docs/MIGRATION.md | Explains what was intentionally migrated from Tags-Project and DebUI/Cortex, what is currently in progress, and what is explicitly deferred. |
| 4 | Data-Retrieval/src/data_retrieval/domain/models.py | Shows the current canonical domain contract and, just as importantly, its present limitations. |
| 5 | Data-Retrieval/docs/decisions/0002-temporal-history-as-atom-projection.md | Defines why Temporal History is a bridge/projection and why its summaries come back as atoms with lineage. |
| 6 | Data-Retrieval/docs/decisions/0005-raw-ingestion-before-enrichment.md | Protects raw evidence from optional AI services and explains why enrichment is derived/retryable. |
| 7 | Data-Retrieval/docs/decisions/0006-staged-retrieval-and-outcome-learning.md | Defines semantic candidate generation, learned expansion, conditional temporal semantics, factual-vs-learned links, and feedback policy. |
| 8 | Data-Retrieval/docs/decisions/0008-replayable-calibration-and-mem0.md | Defines teacher/calibration cold-start structure and Mem0 as a first-class but non-canonical provider. |
| 9 | Data-Retrieval/docs/LONGMEMEVAL.md + docs/PROJECT-HISTORY-ACCEPTANCE.md | Shows what is actually measured, how temporal lineage is credited, and where current retrieval quality is strong or still incomplete. |
| 10 | Tags-Project/chunker.py, retrieve.py, db_operations.py, main.py | Read the original working semantic retrieval idea in code: query-tag generation, canonical-ish tag reuse, tag→chunk inverted retrieval, adjacency/fallback experiments. |
| 11 | DebUI/subprojects/data-memory/docs/VISION.md | States the larger goal: atomic memory units, weighted tag relationships, feedback loops, and skill objects distilled from repeated useful clusters. |
| 12 | DebUI/subprojects/data-memory/docs/LEGACY_TO_CORTEX_MAPPING.md | Maps old tag architecture concepts into Cortex and explicitly lists prerequisites for future skill synthesis. |
| 13 | DebUI/subprojects/data-memory/PLAN2.md and PLAN.md | Captures the high-fidelity recovery of broad/specific tags, tag pairings, GroupTags, catalog-first canonicalization, and future-proof skill synthesis contracts. |
| 14 | DebUI/subprojects/data-memory/cortex/docs/data-science/TAG_GENERATION_SPEC.md | Canonicalization, broad/specific hierarchy, teacher evidence, LLM proposals, proposal states, and provenance. |
| 15 | .../TAG_RELATION_AND_GROUP_SPEC.md | Critical for emergent groups: GroupTag, weighted membership, cohesion, promotion/demotion, reuse/outcome gates, and group evolution. |
| 16 | .../RETRIEVAL_MATH_SPEC.md | Historical hybrid retrieval, pair/group context boosts, adjacency windows, layered expansion, rate-of-change stopping, and fail-safe scans. |
| 17 | .../LEARNING_MATH_SPEC.md | Feedback deltas, conflict buffering, bounded updates, interaction-derived learning, tag-pair/group updates, and adaptive weights. |
| 18 | DebUI/subprojects/data-memory/research/core-tag-functionality/extracted-txt/* | Read the oldest intent directly: Adding New Data, Information Retrieval, MongoDB Schema, Weight Updating Mechanisms, To Do / Possible Features. |
| 19 | DebUI/Perfecting Graph Memory Retrieval.md | Historical design conversation containing Universal Atoms, teacher/student bootstrapping, gravity, hybrid manager, and the Refinery/Gardener idea. Treat as conceptual history, not normative current spec. |
| 20 | Temporal History repository | Study canonical raw events, calendar lattice, thread/channel timelines, summary lineage, pressure compaction, frontier assembly, and progressive drill-down. Do not import its retrieval philosophy wholesale. |

## 5. Current Data-Retrieval: what each important file means

### 5.1 README.md — operational truth of the current branch

Use the README to understand what the current branch already does before proposing new infrastructure. It describes the clean successor to Tags-Project, current storage choices, enrichment commands, retrieval modes, feedback, embeddings, Temporal History projection, PostgreSQL scale mode, and benchmark entry points.

- Notice that the target graph is still centered on Document → Atom, Atom → Tag, Atom → Atom links, and Tag → Tag relationships.

- Notice that Temporal topics are intentionally kept as summary metadata until a calibrated promotion step can safely place them into the tag graph. This is directly relevant to the semantic-temporal routing idea described later.

- Notice that the current retrieval result contains per-channel scores, evidence, temporal roles, and atom IDs. Preserve this explainability when adding groups or skills.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/README.md`*

### 5.2 CONTEXT_LEDGER.md — the architecture memory for the architecture itself

This should be treated as mandatory reading before every substantial architectural change. Its purpose is explicitly to prevent accepted behavior from disappearing during migration or deferral.

- It states that atoms are payload-agnostic in intent.

- It preserves the no-delete/source-evidence invariant.

- It separates factual links from behavioral links.

- It explicitly says ordered procedures/steps are additive and must not be encoded into ordinary associative atom relationships.

- It preserves GroupTags as an accepted next layer.

- It preserves future refinement into concise instructions, extraction of ordered steps/prerequisites/branches/outcomes, and training small models on those structures.

> Important consequence
> The workflow/skill idea discussed today is not a random new direction. The current repository already contains a deliberate placeholder for exactly this future layer. The job now is to recover the intended semantics and design the next layer without corrupting the associative graph.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/docs/CONTEXT_LEDGER.md`*

### 5.3 MIGRATION.md — what was moved, what was deliberately left for later

The migration plan explains that the new system is a selective successor, not a rewrite that invalidates everything old. It attributes different contributions to the older generations.

- Tags-Project contributes the original tag-first retrieval intent.

- DebUI/data-memory contributes the atom model, graph direction, catalog-first canonicalization, hybrid retrieval, and evaluation ideas.

- Temporal atom projection, canonical persistence, tag canonicalization, retrieval, learning/evaluation, and Mem0 calibration are staged milestones.

- Group-tag promotion and skill distillation are explicitly deferred, not rejected.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/docs/MIGRATION.md`*

### 5.4 domain/models.py — present implementation contract, not the final ontology

The current Atom model contains a text content field and a small AtomKind enum (source, temporal_summary, interaction, uncertainty). Do not mistake this for the final payload model. The architecture ledger says atoms are payload-agnostic, and old Cortex had content_type + payload_ref + payload_inline_small.

- AtomLink currently supports SUMMARIZES, DERIVED_FROM, SUPERSEDES, CO_USED, CONFLICTS_WITH, and ADJACENT_TO.

- Tag and AtomTag already support broad/specific levels, canonical/proposed states, raw weights, confidence, origin, and evidence sources.

- CalibrationSignal is immutable/replay-safe evidence for initializing or adjusting atom/tag/link relationships. This is the safe modern replacement for opaque one-off weight mutation.

> Design warning
> If you add skill atoms, groups, or new media payloads, do not simply keep extending a closed enum until every payload type becomes a special case. The desired architecture is a generic atom identity/relationship layer plus handler/add-on metadata that tells consumers how to use the payload.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/src/data_retrieval/domain/models.py`*

### 5.5 ADR-0002 — Temporal History is a projection

This ADR is the boundary rule for temporal integration. Timestamped atoms are converted into Temporal History events; generated summaries are converted back into atoms; lineage is represented with generic atom links; Temporal History state is rebuildable; Data-Retrieval remains canonical.

- Do not run Temporal History as a second canonical memory service.

- Do not duplicate Day/Week/Month entities into the core domain if summary atoms + metadata can represent them.

- Use the bridge to preserve Temporal History’s tested calendar and lineage logic while keeping the graph unified.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/docs/decisions/0002-temporal-history-as-atom-projection.md`*

### 5.6 ADR-0005 — raw ingestion must survive model/provider failure

Raw source capture and AI enrichment are separate stages. This is essential once the system starts generating groups, summaries, workflows, media representations, or skill atoms.

- A derived workflow must never become the only surviving representation of the events that produced it.

- A failed skill synthesizer must not block raw ingestion.

- Temporal topics are not currently auto-promoted to tags because confidence/canonicalization are not strong enough. Any future semantic-temporal propagation must respect this.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/docs/decisions/0005-raw-ingestion-before-enrichment.md`*

### 5.7 ADR-0006 — staged retrieval and learning boundaries

The current intended retrieval sequence is: direct tags + lexical + semantic candidates → learned tag/CO_USED expansion → conditional temporal lens → redundancy removal → auditable retrieval event.

- Structural: SUMMARIZES / DERIVED_FROM / SUPERSEDES.

- Behavioral: CO_USED / tag co_occurs.

- Temporal mode is conditional (none/current_state/as_of/range/history).

- Ordered problem-solving steps are explicitly deferred to a later structure.

> Why this matters for skills
> A future process atom may be retrieved through the same graph, but the ordered steps inside the process should be represented as a structured procedure/state machine, not inferred from the order of CO_USED edges.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/docs/decisions/0006-staged-retrieval-and-outcome-learning.md`*

### 5.8 ADR-0008 — cold start, teacher signals, and Mem0

The graph cannot learn useful relationships from nothing. The current solution is replayable calibration signals from deterministic teachers and Mem0. Mem0 is useful for bootstrapping/distillation, but it is not a serving dependency or source of truth.

- Teacher evidence currently seeds entity density, code structure, corpus rarity, tag priors, tag co-occurrence, hierarchy, and source adjacency.

- Mem0 outputs become native atoms with lineage and deduplication; conflicts become explicit links instead of silent overwrites.

- This same provider pattern should be reused for future group synthesizers and workflow/skill synthesizers: derived outputs become native atoms/links with provider/version/provenance, while source atoms remain authoritative.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/docs/decisions/0008-replayable-calibration-and-mem0.md`*

### 5.9 Evaluation documents — keep architecture claims measurable

The repo now has more than the small smoke benchmarks. LongMemEval ingestion preserves sessions, turns, timestamps, and question isolation, and the full Oracle pipeline reports both lineage-credit and exact-raw evidence metrics. This distinction is critical: retrieving a summary whose lineage covers the answer is not the same as directly retrieving the exact source atom.

- When evaluating future groups, do not count a group hit as exact evidence unless the required source member is actually expanded and available to the downstream consumer.

- When evaluating skills, measure task success and procedural correctness separately from retrieval hit rate.

*Source: `Read: DimitrisKaintasis/Data-Retrieval/docs/LONGMEMEVAL.md`*

*Source: `Read: DimitrisKaintasis/Data-Retrieval/docs/PROJECT-HISTORY-ACCEPTANCE.md`*

## 6. Original Tags-Project: the retrieval idea that must not be lost

The old implementation was rough, but conceptually important. Its defining idea was not “embed chunks and search them.” It used a smaller semantic concept space as a routing layer.

```text
user query
 ↓
LLM generates semantic query tags
 ↓
embed each query tag
 ↓
find nearest existing canonical-ish stored tag
 ↓
follow tag → chunk inverted index
 ↓
retrieve source chunks
 ↓
(optional) adjacency / semantic fail-safe
```

### 6.1 chunker.py — semantic chunking + canonical-ish tag reuse

- Text is pre-chunked to fit model limits, then GPT refines it into coherent sentence/thought units.

- Each chunk gets semantic tags.

- Each generated tag is embedded and compared to the existing tag space. At the old 0.7 threshold, a sufficiently similar existing tag ID/text is reused instead of creating another synonym.

- This is the ancestor of strict catalog-first canonicalization in Cortex/Data-Retrieval.

*Source: `Read: DimitrisKaintasis/Tags-Project/chunker.py`*

### 6.2 retrieve.py — semantic retrieval through tags first

- At query time, tags are generated from the user input.

- The query tags are embedded and mapped to the closest existing stored tags (old threshold 0.6).

- MongoDB then retrieves chunks linked to those stored tags. The active path therefore searches the concept/tag space first, not the raw chunk space first.

- Adjacency retrieval and a broad semantic fail-safe were implemented but commented out in the active main path. Preserve the ideas as optional retrieval controls, not as proof that they were production-ready.

*Source: `Read: DimitrisKaintasis/Tags-Project/retrieve.py`*

### 6.3 db_operations.py — tag → chunk is an inverted semantic index

- Tag records collect associated chunk IDs; retrieving by tags unions those chunk IDs and fetches the source chunks.

- Existing-tag reuse increments frequency and associates new chunks with the same concept node.

- This is why tags should be treated as stable semantic handles, not decorative labels.

*Source: `Read: DimitrisKaintasis/Tags-Project/db_operations.py`*

### 6.4 main.py — ingestion kept both chunk embeddings and tag routing

- The old system embedded chunks too, but its distinctive retrieval path was still tag-first.

- Do not reduce the new architecture to “pgvector plus metadata” and accidentally discard this original purpose.

*Source: `Read: DimitrisKaintasis/Tags-Project/main.py`*

## 7. DebUI / data-memory / Cortex: recovered architecture intent

The most important historical material is not the standalone Cortex repo. It lives inside DebUI under subprojects/data-memory and in a few root-level design-conversation artifacts. This material explains why the current Data-Retrieval context ledger contains group and procedural placeholders.

### 7.1 VISION.md — memory graph, feedback, and skill objects

This is the clearest short statement of the intended direction: the system is explicitly “not a plain vector store.” It combines atomic memory units, weighted tag relationships, retrieval feedback loops, and distillation into stable reusable skills. One success criterion is that skill objects are produced from usage patterns rather than manual curation only.

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/docs/VISION.md`*

### 7.2 LEGACY_TO_CORTEX_MAPPING.md — the bridge between generations

- Maps broad/specific hierarchical tags, tag-to-tag matching weights, GroupTags, adjacency retrieval, layered/RoC progression, fail-safe scans, interaction learning, and skill synthesis prerequisites.

- Skill-synthesis prerequisites were explicitly preserved: per-tag provenance, canonical tags/alias history, tag-pair temporal update history, group cohesion/membership evolution, outcome-linked usage counters across atoms/tags/groups, and replayable audit traces.

> Interpretation
> The system was deliberately collecting not only “what is related” but also “how relationships and groups evolve through successful use,” because that history is the evidence needed to later infer reusable skills.

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/docs/LEGACY_TO_CORTEX_MAPPING.md`*

### 7.3 PLAN2.md / PLAN.md — high-fidelity tag-core recovery

- Decision-locked broad/specific tagging and parent relationships.

- Tag pairings and GroupTags as first-class graph structures.

- Strict catalog-first semantic canonicalization: match to existing concepts before proposing new ones.

- Unbounded raw relationship weights with normalized retrieval views.

- Skill synthesis deliberately out of MVP, but data contracts designed to avoid a future schema rewrite.

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/PLAN2.md`*

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/PLAN.md`*

### 7.4 TAG_GENERATION_SPEC.md — tags are structured concept objects

- Deterministic teacher hints + LLM proposals + normalization + semantic catalog matching + proposal state + provenance.

- Text, code, and event atoms have different tagging expectations, but share the same tag graph.

- Broad and specific tags are intentional: retrieval can enter at a broad concept and drill into narrower concepts.

- Tags include evidence, confidence, aliases, origin, and generation trace; they are not strings without provenance.

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/data-science/TAG_GENERATION_SPEC.md`*

### 7.5 TAG_RELATION_AND_GROUP_SPEC.md — the missing emergent-group layer

This is essential reading. It explicitly says “GroupTag models emergent tag clusters.” A group is not meant to be a manually declared folder.

- Member tags have internal weights representing contribution to the group.

- The group has a cohesion score representing stability during an evaluation window.

- Promotion requires minimum cohesion, reuse count, and outcome quality.

- Demotion/deactivation uses failed windows with hysteresis, so groups do not flap in and out from short-term noise.

- All relation/group changes require lineage and replayability.

> Today’s generalization
> The old spec formalized emergent groups only for tags. The current design discussion generalizes the same pattern to arbitrary atoms: stable useful clusters of knowledge atoms, process atoms, tools, media, and other groups can become higher-order group objects that participate in retrieval themselves.

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/data-science/TAG_RELATION_AND_GROUP_SPEC.md`*

### 7.6 RETRIEVAL_MATH_SPEC.md — graph traversal was meant to be active

- Historical Cortex fused graph, lexical, and semantic channels.

- Tag-pair and group context were intended to provide bounded score boosts.

- Adjacency windows recovered local surrounding context around strong hits.

- Layered tag expansion recursively explored related concepts rather than performing only a one-shot nearest-neighbor lookup.

- Rate-of-change stopping and fail-safe broader scans were mechanisms for controlling expansion and recovering when the graph was weak.

> Do not copy the old formula blindly
> The useful idea is dynamic graph expansion with explicit stopping/fallback logic. Thresholds and formulas should be recalibrated on the current evaluation corpus, not copied because they existed historically.

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/data-science/RETRIEVAL_MATH_SPEC.md`*

### 7.7 LEARNING_MATH_SPEC.md — relationship structure is supposed to evolve

- Interaction outcomes update graph weights; uncertain conflicts are buffered instead of immediately polarizing the graph.

- The target extension included HAS_TAG, RELATED_TO, GroupTag membership/cohesion updates, and negative-signal forgetting.

- The key preserved principle is outcome-based adaptation with bounded/replayable updates, not the exact old delta numbers.

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/data-science/LEARNING_MATH_SPEC.md`*

### 7.8 GRAPH_SCHEMA.md — older payload-agnostic storage clue

Old Cortex already separated an Atom’s graph identity from how its payload was stored. Atom properties included content_type, payload_ref, and payload_inline_small. This is strong evidence for the long-term payload-agnostic interpretation.

- A new modality should not require a new top-level memory system. It should require an atom representation plus an application/add-on that knows how to interpret the payload reference.

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/schemas/GRAPH_SCHEMA.md`*

### 7.9 Old extracted design files — read the intent in its earliest form

| File | What it contributes |
| --- | --- |
| Adding New Data.txt | Predefined/canonical tags, hierarchical broad/specific tagging, tag propagation from repeated co-use, dynamic weight-based broad-category inheritance, modality-specific ingestion, code AST segmentation, IDs/versioning, chat/event ingestion. |
| Information Retrieval.txt | Query tag generation, nearest existing-tag replacement, direct tag retrieval, adjacency expansion, semantic fail-safe, recursively layered tag matching, rate-of-change stopping, code/dependency-aware retrieval. |
| MongoDB Schema.txt | The earliest explicit Tag Pairings and Group Tags collections; Group Tags contain member tag IDs with internal weights. |
| Weight Updating Mechanisms.txt | Sentence↔tag weights plus tag↔tag matching weights; interaction-based strengthening, periodic updates, usage counters, decay ideas. |
| To Do / Possible Features.txt | Updated-information priority, conversation continuity, counting, and automatically assigning tags based on tags that repeatedly proved useful together. |

*Source: `Read: DimitrisKaintasis/DebUI/subprojects/data-memory/research/core-tag-functionality/extracted-txt/`*

### 7.10 Perfecting Graph Memory Retrieval.md — conceptual history, not current law

This root-level conversation artifact contains several ideas that later reappear in more disciplined form. Read it to recover intent, but reconcile every idea against the current context ledger and ADRs.

- Universal Atoms: raw data of many kinds should enter a common graph abstraction instead of separate code/text/image memory products.

- Teacher–Student bootstrapping: deterministic heuristics provide “instinct” before outcome learning exists. Current Data-Retrieval CalibrationSignal is the safer, replayable descendant of this idea.

- Gravity/importance: historical attempt to combine freshness/use/topology/user intent. Do not restore it automatically; current retrieval has a more explainable staged design and any importance signal must earn its place in evaluation.

- Refinery/Gardener: frequently useful raw material is distilled into concise imperative instructions so small models can execute pre-digested logic. The modern version must be additive: create derived process/skill atoms; never replace or delete the source atoms.

- Hybrid Manager self-correction: if deterministic channels succeed where graph retrieval fails, learn from the attributable outcome. The modern version should use explicit calibration/feedback rather than automatic reinforcement of every retrieved result.

*Source: `Read: DimitrisKaintasis/DebUI/Perfecting Graph Memory Retrieval.md`*

## 8. Clarified target architecture from today’s design discussion

The following points are the most important intentions that were not sufficiently understood by the current implementation agent. They should be treated as design requirements for future planning, while still being introduced incrementally and evaluated.

### 8.1 The graph is one substrate for many kinds of information and capability

```text
┌────────────────────┐
 │ Graph / Retrieval │
 │ identity + relations │
 └─────────┬───────────┘
 │
 ┌───────────────┬───────┼────────┬──────────────┐
 │ │ │ │ │
 text atom image audio code atom process/skill
 │ │ │ │ │
 inline/ref payload payload source/ref structured steps
 │ │ │ │ │
 └──────────── specialized add-ons / consumers ───┘
```

An atom should be thought of as an addressable graph object with stable identity, provenance, metadata, semantic relationships, and a payload or payload reference. The graph does not need to natively “understand” every payload. Specialized consumers/add-ons do that.

### 8.2 Atoms should remain flexible; add-ons supply behavior

| Retrieved atom type | Core graph responsibility | Example add-on responsibility |
| --- | --- | --- |
| Text / document | Identity, tags, lineage, timing, relevance | Pack text into context; optionally summarize or quote source. |
| Image | Identity, tags, links, source/payload locator | Load image and pass to a vision-capable model or image processor. |
| Audio | Identity, tags, links, source/payload locator | Transcribe, classify, or pass to an audio model. |
| Code | Identity, project/file/commit links, tags, adjacency/dependency relationships | AST-aware reconstruction, code tool invocation, repository navigation. |
| Temporal summary | Derived atom + lineage + period metadata | Chronology reconstruction, drill-down, current/as-of/history handling. |
| Process / skill | Retrievable atom + tags + provenance + applicability | Instantiate and execute a structured workflow/state machine. |
| Group / capability | Higher-order retrievable object + weighted membership | Expand relevant members under a retrieval/execution budget. |

### 8.3 Semantic retrieval remains the primary entry into memory

The key idea from Tags-Project remains: the user should not need to know where or when something was stored. Query concepts activate stored concepts; concepts route to relevant atoms. Time, source, thread, and other dimensions refine that semantic region.

```text
query
 ↓
query concepts / tags
 ↓
canonical tag graph + embeddings + lexical signals
 ↓
relevant atoms / groups / temporal regions / skills
 ↓
application-specific expansion and interpretation
 ↓
exact source evidence or executable capability
```

### 8.4 Temporal History complements semantic memory instead of replacing it

Temporal History solves chronology, compression, state reconstruction, and provenance. It does not by itself solve the “unknown unknown” problem: a model may not know that some obscure omitted detail exists and therefore may never decide to drill into the right historical region. Semantic routing supplies the clue; temporal structure supplies the history once the clue is found.

### 8.5 Learned association, factual lineage, and procedure are three different edge meanings

| Category | Examples | Interpretation |
| --- | --- | --- |
| Factual / provenance | DERIVED_FROM, SUMMARIZES, SUPERSEDES, CONFLICTS_WITH, ADJACENT_TO | Claims about source history or evidence structure. Do not mutate because a user liked a retrieval. |
| Learned / behavioral | CO_USED, tag co_occurs, learned affinity | Evidence that two things repeatedly help together. May change with outcome feedback. |
| Procedural | REQUIRES, PRECEDES, PRODUCES, VALIDATES, BRANCHES_TO | Ordered execution semantics. Should live inside or alongside process atoms, not be inferred from CO_USED alone. |

## 9. Planned semantic-temporal retrieval enhancement

The current runtime primarily discovers relevant candidates and then applies a temporal lens. A stronger future design is to use the semantic/tag mechanism recursively inside the temporal lattice itself.

```text
query concepts
 ↓
score coarse temporal nodes (year/month/etc.) by semantic/tag relevance
 ↓
keep several promising branches (beam, not one greedy branch)
 ↓
inside each branch, score finer periods using the same concept graph
 ↓
optionally expand the query with newly discovered high-confidence concepts
 ↓
continue until source atoms / exact evidence are reached
 ↓
reconstruct chronology and state transitions
```

### 9.1 Important implementation constraints

- Do not blindly promote LLM summary topics into canonical tags. Aggregate source-tag evidence upward when possible; normalize/canonicalize any new summary-derived concept before entering the tag graph.

- Do not use a greedy single-path descent. A question about an evolving idea may need March (proposal), September (failure), and November (replacement). Keep a bounded beam of relevant temporal branches.

- Allow query evolution only under a strict budget. Newly discovered concepts may become retrieval cues, but cap concept count, branch width, depth, and total candidate expansion.

- At the end, distinguish summary-lineage coverage from exact source evidence. A broad summary is useful continuity, not necessarily the direct proof required by the answer.

- Current-state questions must not allow historical continuity summaries to be presented as current truth. Preserve temporal roles in the context pack.

### 9.2 Suggested temporal-node semantic profile

```text
Temporal summary atom
- period_start / period_end / resolution / timeline_id
- summary text
- source / child lineage
- canonical tags inherited or aggregated from descendants
- per-tag support count / weight / confidence
- first_seen / last_seen for concepts (optional)
- embedding(s) as replaceable derived representations
- current/historical role metadata
- generation provider + version
```

## 10. Emergent atom groups: generalizing the old GroupTag idea

The old Cortex GroupTag concept should be treated as the prototype for a more general higher-order abstraction. Stable groups can emerge from repeated useful structure, and the group itself can behave atom-like at retrieval time.

### 10.1 What a group means

A group is not merely a folder and not merely a text summary. It is a derived higher-order object representing a stable, reusable cluster of members whose relationships have repeatedly mattered together.

```text
Atom A ─┐
Atom B ─┼──> Group G ──> participates in tags, embeddings,
Atom C ─┤ retrieval, other groups, and skills
Atom D ─┘
```

### 10.2 Candidate group lifecycle

| State | Meaning |
| --- | --- |
| Observed pattern | Members frequently co-retrieve/co-use, but evidence is too weak to create an object. |
| Candidate group | A derived group record/atom exists for evaluation; membership and cohesion are tracked. |
| Stable group | Meets minimum cohesion, reuse, outcome-lift, and counterexample requirements. |
| Dormant / demoted | Pattern stopped being useful or cohesion degraded. Keep lineage/history; reduce retrieval priority rather than deleting source members. |

### 10.3 Group promotion evidence

- Member co-use frequency and outcome quality, not frequency alone.

- Cohesion/stability across time windows.

- Member contribution weights so peripheral members do not define the group.

- Counterexamples: successful outcomes where the supposed group was unnecessary, or failures where the grouped combination was harmful.

- Semantic coherence and/or task coherence.

- Provenance: which interactions, retrievals, workflows, and feedback events caused the group to be proposed/promoted.

### 10.4 Recursive groups and capability hierarchies

```text
DB connectivity ─┐
Schema diagnosis ─┼──> Database Operations ─┐
Migration repair ─┤ │
Backup/restore ──┘ ├──> Backend Operations
Linux services ─────────────────────────────┤
Networking ─────────────────────────────────┘
```

This recursive property is powerful: the same primitive can represent semantic domains, memory clusters, workflows, skill sets, and higher-level capabilities. The retrieval system can surface the group first and expand only the relevant members under budget.

## 11. Process and skill atoms: procedural memory inside the same graph

Processes should be stored as atoms too. That lets a single retrieval return factual context, historical evidence, tools, and procedures together. The difference is not whether the object is an atom; the difference is which add-on interprets the retrieved payload.

### 11.1 What a process/skill atom should contain

```text
Skill atom
- identity / version
- name + goal
- applicability / preconditions
- required inputs
- ordered steps
- expected output per step
- validation checks
- failure conditions
- alternative branches / recovery paths
- required tools or child skills
- success criteria
- evidence episodes / DERIVED_FROM lineage
- confidence and support count
- known counterexamples / exceptions
- last validated environment/version
- tags / embedding / namespace / project scope
```

### 11.2 How skills should be synthesized

1. Observe repeated successful episodes: retrieved atoms, tools used, actions taken, outcomes, and relevant temporal state.

1. Cluster episodes that appear to instantiate the same underlying task/workflow.

1. Use a capable synthesis model to propose the common procedure, preconditions, branches, validations, and exceptions.

1. Validate the proposed workflow against source episodes and counterexamples; reject or keep as candidate if support is weak.

1. Persist the result as a derived process/skill atom with complete lineage to its evidence.

1. Allow the normal graph to retrieve the skill atom by tags/relationships just like other knowledge.

1. A skill add-on instantiates the structured procedure for the current state and hands concrete steps to an execution model.

1. Record execution outcome; update learned relationships and skill confidence without rewriting the source evidence.

### 11.3 Skill sets should emerge as groups of skill atoms

If several skills repeatedly appear together for successful tasks, they can form an emergent capability group using the same group machinery. Avoid building a separate hand-maintained “skill taxonomy” unless a product requires it.

## 12. Small-model enhancement: externalize reasoning into reusable procedures

A major intended benefit is not simply “better memory for a big model.” It is to shift expensive reasoning from every execution into reusable distilled structures. A large/strong model may synthesize or validate a process once; a smaller, cheaper model can then execute the concrete plan many times.

```text
Without procedural memory:
small model + raw docs → must infer goal, dependencies, order, branches, validation

With procedural memory:
retrieval → skill atom → concrete state-aware procedure → small model executes next step
```

### 12.1 Why this can outperform ordinary RAG for small agents

- RAG supplies relevant information but still asks the model to synthesize a plan.

- A process atom supplies an already validated execution structure: goal, prerequisites, exact steps, branch rules, and checks.

- The small model’s task becomes local classification + constrained execution instead of open-ended planning.

- Groups can provide the relevant skill set automatically when a task spans several procedures.

- Historical atoms can explain why the workflow exists, while source evidence remains available for verification.

### 12.2 Do not store private chain-of-thought as the procedure

Distill observable, auditable procedural knowledge: steps, dependencies, checks, tool calls, state transitions, and outcomes. The valuable artifact is an executable procedure, not an opaque transcript of a model’s hidden reasoning.

## 13. Add-on architecture for heterogeneous payloads

The core graph should remain generic. A retrieved atom can advertise a handler/add-on key or content semantics; the calling product selects the appropriate consumer.

```text
retrieve atoms
 ↓
classify by handler / atom semantics
 ├─ text_context → pack for language model
 ├─ image → vision loader
 ├─ audio → audio/transcription path
 ├─ code → repository/AST tools
 ├─ temporal → history assembler
 ├─ skill → skill executor / state machine
 └─ group → bounded member expander
```

### 13.1 Core fields to preserve for payload independence

- stable atom_id and namespace

- content/payload hash

- payload locator or inline-small representation

- content/handler type

- metadata and source reference

- created_at and occurred_at where meaningful

- provenance/lineage links

- tags and learned relationships

- replaceable embeddings/representations keyed by model/profile

The old Cortex payload_ref/payload_inline_small pattern is a useful reference. The current text content field may remain for text-first development, but the architecture should not assume that all future atoms are UTF-8 chunks.

## 14. Relationship taxonomy and safety boundaries

| Relationship family | Examples / possible extensions | Update authority |
| --- | --- | --- |
| Source / factual | DERIVED_FROM, SUMMARIZES, SUPERSEDES, CONFLICTS_WITH, ADJACENT_TO | Source adapters, deterministic inference, validated model-derived provenance. Never changed just because retrieval was liked. |
| Semantic / learned | HAS_TAG weight, tag co_occurs, CO_USED, learned affinity | Feedback, calibration, outcomes, replay-safe learning signals. |
| Group membership | CONTAINS/member_weight, cohesion/support | Group detector + promotion/demotion quality gate with provenance. |
| Procedural | REQUIRES, PRECEDES, PRODUCES, VALIDATES, ALTERNATIVE_TO, RECOVERS_WITH | Workflow synthesizer/validator. Prefer structured process payload where ordering/branching is complex. |
| Temporal | period membership, first/last seen, state role | Temporal bridge/lens and deterministic metadata. |

> Key rule
> Never infer causality, procedural order, or factual supersession merely from co-use. Learned association is evidence that two things help together; it is not proof of why, when, or in what order they must be used.

## 15. Implementation status: do not confuse “missing” with “unwanted”

| Capability | Status | Agent interpretation |
| --- | --- | --- |
| Canonical atoms/documents/tags | Implemented | Core identity, source ordering, tag states/weights/provenance. |
| Raw ingestion before enrichment | Implemented | Provider failure does not lose source data. |
| Catalog-first tags | Implemented / maturing | Exact + semantic match before proposed_new. |
| Lexical/tag/semantic candidate retrieval | Implemented | Primary relevance channels. |
| Learned tag/CO_USED expansion | Implemented | Behavioral associations after evidence/feedback. |
| Temporal summary atoms + lineage | Implemented | Temporal History as derived projection. |
| current_state / as_of / range / history | Implemented | Conditional temporal semantics. |
| Mem0 as calibration/distillation provider | Implemented | Native atoms remain authoritative. |
| LongMemEval-scale ingestion/evaluation | Implemented | Use exact-vs-lineage metrics carefully. |
| Explicit GroupTags / automatic group promotion | Accepted / deferred | Old Cortex semantics preserved; quality gate required. |
| Generalized AtomGroups (heterogeneous members) | Clarified today | Generalization of GroupTag; needs ADR/data-model proposal. |
| Semantic-guided traversal within temporal hierarchy | Clarified today | Research/next retrieval layer; current temporal lens is later-stage filtering. |
| Process / skill atoms | Accepted concept + clarified today | Current ledger already preserves ordered procedure extraction as future structure. |
| Skill-set groups / group-of-groups | Clarified today | Use group machinery recursively. |
| Small-model executor add-on | Clarified today | Consume structured skill atoms; benchmark capability/cost. |
| General multimodal payload adapters | Accepted direction / partial historical model | Old Cortex payload_ref pattern; current model still text-centric. |

## 16. What I want the implementation agent to do next

Do not immediately implement all future layers. First produce an architecture reconciliation based on the reading above.

1. Read the mandatory sources and write a short “current implementation vs preserved intent” map. Cite exact repo paths for every claim.

2. Confirm which old concepts are already represented by the current context ledger and which today’s clarified concepts require a new ADR or ledger update.

3. Propose how to generalize payload handling without breaking current text-first ingestion. Specifically address payload_ref/handler semantics and model-specific derived representations.

4. Propose a generalized group representation. Compare (a) separate Group entity, (b) Group-as-Atom + weighted CONTAINS links, and (c) Tag-only GroupTag. Explain retrieval, provenance, lifecycle, and recursion implications.

5. Design group promotion/demotion evaluation before implementing automatic group creation. Define cohesion, reuse, outcome lift, counterexamples, and hysteresis.

6. Design the process/skill atom schema separately from associative links. Include applicability, ordered steps, branches, validations, tools, evidence lineage, confidence, exceptions, and versioning.

7. Design a skill-consumer add-on that converts retrieved process atoms into an execution plan for a small model without teaching the core retriever how to execute arbitrary workflows.

8. Propose a workflow synthesis pipeline using successful episodes/retrievals/tool outcomes. Include validation and rejection conditions; do not promote every repeated sequence.

9. Propose a semantic-temporal beam traversal experiment that is additive to the current temporal lens. Specify budgets and an ablation against current retrieval.

10. Extend evaluation planning: groups and skills must be measured for task success, exact evidence access, cost, latency, and small-model uplift — not only hit@k.

### 16.1 Questions the agent must answer before changing the core schema

- What remains canonical if an atom points to an external image/audio/code object that can move or change?

- Should AtomKind represent semantic role (source/summary/skill/group) while content_type/handler describes payload modality?

- How is a group represented so it can be retrieved atom-like without duplicating all member content?

- How do group tags/embeddings update when membership changes?

- How do we prevent group-of-group recursion from exploding retrieval?

- How do we preserve exact raw evidence when a high-level group or summary is retrieved?

- How are candidate skills validated against counterexamples and environment/version changes?

- Which procedure semantics belong inside a structured skill payload versus graph edges?

- How should feedback on a skill propagate to the skill, its member atoms, and source episodes without double counting?

- How can temporal summary concepts become semantically searchable without polluting the canonical tag catalog?

## 17. Anti-goals: mistakes that would erase the project’s distinctive architecture

- Do not turn Data-Retrieval into “pgvector + BM25 + metadata” and call the tag graph optional decoration.

- Do not make Temporal History the primary address space for ordinary recall.

- Do not create separate disconnected databases for memories, skills, media, and groups if the atom abstraction can unify their graph identity.

- Do not let a derived summary, group, Mem0 memory, or skill replace its raw evidence.

- Do not automatically reward every retrieval; that creates a self-confirming graph.

- Do not interpret co-occurrence as causality or procedural ordering.

- Do not copy old thresholds/formulas because they are old; keep the mechanism, recalibrate the numbers.

- Do not blindly auto-promote summary topics or LLM-generated group labels into canonical tags.

- Do not require Neo4j merely because the logical model is a graph. Storage should follow measured query requirements.

- Do not hard-wire a single embedding or LLM provider into domain semantics.

- Do not implement “refinement” by deleting raw atoms. Modern refinement means creating derived concise/skill atoms with lineage.

## 18. Evaluation plan for the next architecture layers

### 18.1 Semantic-temporal traversal ablation

| Variant | Purpose |
| --- | --- |
| Current retrieval | Tags/lexical/semantic candidates + learned expansion + temporal lens. |
| Temporal-only progressive retrieval | Measure what chronology alone contributes. |
| Semantic-guided temporal beam | Test recursive tag routing across time levels. |
| Semantic-guided temporal beam + query expansion | Test whether discovered concepts improve vague/weak-cue recall without runaway search. |

Measure exact source recall@k, lineage recall@k, state correctness, temporal ordering accuracy, retrieval depth, branches expanded, tokens, latency, and total inference cost.

### 18.2 Group quality gates

- Does retrieving the group improve precision/recall or reduce context cost compared with retrieving members independently?

- Does the group remain stable across time windows?

- Does expanding the group expose the exact evidence needed?

- Does group promotion improve downstream task success, not merely create aesthetically coherent clusters?

- What is the false-group rate under unrelated but frequent co-occurrence?

### 18.3 Skill / small-model benchmark

| Condition | What it tests |
| --- | --- |
| Small model alone | Baseline planning/reasoning capability. |
| Small model + ordinary RAG | Effect of relevant information without procedure distillation. |
| Small model + current Data-Retrieval evidence pack | Effect of associative/temporal retrieval. |
| Small model + retrieved skill atom | Direct value of procedural memory. |
| Small model + skill-set group | Value of automatically composing several relevant procedures. |
| Large model baseline | Upper comparison point for task success and cost. |

Measure end-to-end task success, invalid tool/action rate, number of recovery loops, required human intervention, token use, latency, monetary cost, and whether the agent followed validation/failure branches correctly.

## 19. Working glossary for the next development phase

| Term | Use this meaning |
| --- | --- |
| Atom | Stable, addressable graph-level unit of information/capability with identity, metadata, provenance, relationships, and a payload or payload locator. |
| Source atom | Canonical captured evidence from an external/source system. |
| Derived atom | Summary, distilled memory, group representation, skill, or other object generated from source atoms with lineage. |
| Tag | Canonical semantic concept handle used for routing and learning; not just display metadata. |
| Learned relationship | Mutable association supported by usage/outcomes/calibration, e.g. CO_USED or tag co-occurrence. |
| Factual relationship | Evidence/provenance/state claim such as DERIVED_FROM or SUPERSEDES. |
| Group | Higher-order derived object representing a stable useful cluster of members with weighted membership/cohesion and provenance. |
| Process / skill atom | Derived procedural-memory atom containing reusable ordered execution structure and evidence. |
| Skill set | Emergent group whose members are process/skill atoms that repeatedly compose successfully. |
| Temporal projection | Rebuildable historical organization/summarization derived from timestamped canonical atoms. |
| Add-on / handler | Application-specific consumer that knows how to interpret a retrieved atom payload or semantic role. |
| Semantic-temporal traversal | Retrieval that uses semantic/tag relevance to choose and recursively refine branches of the temporal hierarchy. |

## 20. One-sentence architecture intent

> Use this as the sanity check for future design decisions
> Data-Retrieval should be a single auditable graph substrate in which arbitrary evidence and capabilities become atoms, semantic tags and learned relationships decide what becomes relevant, temporal structure reconstructs when and how truth changed, stable recurring patterns become higher-order groups, successful problem-solving can be distilled into retrievable process/skill atoms, and specialized add-ons turn those retrieved atoms into useful context or executable behavior — while raw evidence remains intact and traceable.

## Appendix A. Source map with exact repository paths

| Role | Path |
| --- | --- |
| Current | DimitrisKaintasis/Data-Retrieval/README.md |
| Current | DimitrisKaintasis/Data-Retrieval/docs/CONTEXT_LEDGER.md |
| Current | DimitrisKaintasis/Data-Retrieval/docs/MIGRATION.md |
| Current | DimitrisKaintasis/Data-Retrieval/src/data_retrieval/domain/models.py |
| Current | DimitrisKaintasis/Data-Retrieval/docs/decisions/0002-temporal-history-as-atom-projection.md |
| Current | DimitrisKaintasis/Data-Retrieval/docs/decisions/0005-raw-ingestion-before-enrichment.md |
| Current | DimitrisKaintasis/Data-Retrieval/docs/decisions/0006-staged-retrieval-and-outcome-learning.md |
| Current | DimitrisKaintasis/Data-Retrieval/docs/decisions/0008-replayable-calibration-and-mem0.md |
| Current | DimitrisKaintasis/Data-Retrieval/docs/LONGMEMEVAL.md |
| Current | DimitrisKaintasis/Data-Retrieval/docs/PROJECT-HISTORY-ACCEPTANCE.md |
| Original | DimitrisKaintasis/Tags-Project/chunker.py |
| Original | DimitrisKaintasis/Tags-Project/retrieve.py |
| Original | DimitrisKaintasis/Tags-Project/db_operations.py |
| Original | DimitrisKaintasis/Tags-Project/main.py |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/docs/VISION.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/docs/LEGACY_TO_CORTEX_MAPPING.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/PLAN.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/PLAN2.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/GLOSSARY.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/schemas/GRAPH_SCHEMA.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/data-science/TAG_GENERATION_SPEC.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/data-science/TAG_RELATION_AND_GROUP_SPEC.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/data-science/RETRIEVAL_MATH_SPEC.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/cortex/docs/data-science/LEARNING_MATH_SPEC.md |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/research/core-tag-functionality/extracted-txt/Adding New Data.txt |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/research/core-tag-functionality/extracted-txt/Information Retrieval.txt |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/research/core-tag-functionality/extracted-txt/MongoDB Schema.txt |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/research/core-tag-functionality/extracted-txt/Weight Updating Mechanisms.txt |
| Historical | DimitrisKaintasis/DebUI/subprojects/data-memory/research/core-tag-functionality/extracted-txt/To Do _ Possible Features.txt |
| Historical | DimitrisKaintasis/DebUI/Perfecting Graph Memory Retrieval.md |
| Incorporated reference | Vendored Temporal History runtime in `src/temporal_history/` |

## Appendix B. Handoff instruction

After reading the sources above, return an architecture reconciliation before making broad changes. The reconciliation should explicitly separate: (1) what is implemented now, (2) what current docs already accept but defer, (3) what historical design should be recovered in modern form, and (4) what today’s clarified direction requires as a new documented decision. Any proposal that cannot identify its evidence in those four categories is not ready to alter the core architecture.

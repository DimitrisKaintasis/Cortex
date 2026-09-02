# ADR-0013: Shared routing intelligence, private evidence

## Status

Accepted as future-scope architecture on 2026-09-02. Not yet implemented.

## Context

The original product scope extended beyond isolated personal graphs. Successful use by one
person was intended to improve a shared semantic graph and therefore help other users and future
models. Recent architecture documents retained global/project/user blending as an experiment
but did not define collective contribution, privacy, or global promotion semantics.

## Decision

1. Data Retrieval's long-term product is a privacy-preserving, model-agnostic collective
   capability substrate, not only a memory tool.
2. The system is one logical layered graph: globally stable concepts and promoted capability
   combine with organization, project, user, and private evidence overlays.
3. Private payloads, atom attachments, raw interactions, and identifiers do not become globally
   visible by default.
4. Individual outcomes create scoped immutable observations. They never mutate global serving
   edges directly.
5. Only minimized, consented, sensitivity-checked, bounded observations may enter collective
   aggregation. Rare concept pairs require stronger privacy controls.
6. Global updates require independent-contributor, quality, privacy, and abuse gates and are
   published through versioned shadow/canary serving snapshots with rollback.
7. Shared behavioral associations route attention; they do not assert factual truth, causality,
   or procedural order.
8. Procedures remain a separate validated structure and are the main path for transferring
   ordered problem-solving capability to smaller models.
9. One logical graph does not imply one physical database server. Storage remains partitionable
   behind stable domain contracts.
10. Evidence is immutable to processors and learning, but owner-authorized retention,
    tombstoning, export, erasure, and contribution-revocation policy remain mandatory.

## Consequences

- One user's outcomes can improve another user's retrieval without transferring source atoms.
- Global concept identity must be separated from namespace and visibility.
- Namespace isolation alone is not a security or scope model.
- A contribution and aggregation plane is required before autonomous global learning.
- Concept-pair updates still carry semantic privacy risk; "no raw payload" is not equivalent to
  anonymity.
- The payload-reference contract must include scope, visibility, and contribution policy.
- Secure aggregation/privacy choices must state whether an accepted contribution can later be
  individually removed.

## Rejected alternatives

### Put every user's atoms in one flat global graph

This maximizes central access but violates the intended privacy boundary and makes contextual
conflicts and deletion obligations unsafe.

### Keep every graph completely isolated

This preserves privacy but abandons collective capability transfer and forces every user and
model to relearn the same routing behavior.

### Let each outcome update the global graph immediately

This is vulnerable to noise, popularity bias, privacy leakage, repeated-user domination, and
coordinated poisoning.

## Authority

The detailed contract and validation gate are in `docs/COLLECTIVE-CAPABILITY-GRAPH.md`.

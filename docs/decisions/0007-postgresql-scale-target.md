# ADR-0007: PostgreSQL and pgvector as the scale target

- Status: Accepted
- Date: 2026-08-20
- Extends: ADR-0003's SQLite-first decision

## Context

SQLite established restart-safe canonical persistence with minimal operations. The next
benchmarks include LongMemEval and EverMemBench, and the eventual system must let a remote
Mac worker continue processing against online storage. The existing implementation could
not scale merely by changing databases because it loaded every atom, edge, tag, and vector
in a namespace into Python and read each input file into one string.

The domain is graph-shaped, but the complete workload is broader than graph traversal:
transactional ingestion, bounded relationship expansion, temporal ranges, full-text and
vector search, feedback updates, audit events, and durable worker coordination all share
one canonical state.

## Decision

1. Keep SQLite as the zero-service local development and deterministic test adapter.
2. Use PostgreSQL with pgvector as the target canonical online adapter.
3. Keep graph relationships explicit in `atom_tags`, `atom_links`, and `tag_relations`.
4. Push initial tag, lexical, and semantic candidate generation into bounded indexed
   repository queries. Python retains beam expansion, temporal interpretation, scoring,
   and evidence packing.
5. Store vectors by atom, provider, model, dimension, and content hash. Create a partial
   HNSW index per provider/model/dimension where pgvector supports that dimension.
6. Ingest large files in deterministic batches. A staged document is invisible to normal
   reads until the final persisted atom count is verified and the document is published.
7. Stream embedding enrichment by atom batches rather than hydrating a namespace.
8. Keep large immutable source archives eligible for later S3-compatible object storage;
   PostgreSQL owns their identities, extracted atoms, provenance, and derived state.
9. Keep the Mac an inference/worker host. It does not become canonical storage.

## Alternatives

### Neo4j as canonical storage

Neo4j represents paths naturally, but the current traversal is deliberately bounded and
application-controlled. Adopting Neo4j would add specialized operations and make the other
relational, temporal, vector, job, and audit workloads less uniform before graph traversal
has been measured as the bottleneck.

### ScyllaDB as canonical storage

ScyllaDB is attractive for extreme append throughput, but its query-first partition model
does not fit evolving multi-hop retrieval. It remains a possible future event projection,
not a canonical memory store.

### Split canonical state between graph, vector, and document databases

This can optimize individual channels but creates synchronization, recovery, and ownership
problems. Rebuildable projections remain possible after a measured need appears.

## Consequences

- PostgreSQL requires a service, credentials, backups, monitoring, and controlled schema
  migrations; it is not as operationally simple as a SQLite file.
- Edge traversal is less concise than Cypher, but bounded indexed lookups keep it explicit
  and testable.
- Candidate limits make retrieval memory use independent of namespace size, but quality
  must be calibrated against large labeled datasets.
- Staged ingestion provides bounded transactions and safe retries, but a failed attempt
  may leave hidden staging rows until it is retried or cleaned by a future maintenance job.
- HNSW is approximate and model-specific. Exact vector search remains the fallback for
  dimensions that cannot use an HNSW index.
- A production deployment still needs capacity tests, backup restoration tests, and an
  online storage provider decision before the Mac can work while the laptop is off.

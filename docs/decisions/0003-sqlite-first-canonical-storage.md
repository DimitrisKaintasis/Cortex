# ADR-0003: SQLite-first canonical storage

- Status: Accepted
- Date: 2026-08-19
- Supersedes: ADR-0001's decision to make Neo4j the initial canonical store

## Context

The domain now consists of documents, atoms, tags, weighted atom-tag edges, and
generic atom lineage. Temporal History adds a rebuildable generation cache, but
its summaries are converted back into canonical atoms and links.

The project needs restart-safe persistence before it needs a network database.
The current graph fits relational tables and indexed adjacency queries without
requiring a database server. Operational simplicity matters because the system
will run across a laptop and a remotely hosted Mac with limited shared resources.

## Decision

Use one SQLite database as the first durable canonical store. The adapter:

1. persists a complete ingestion or projection bundle in one transaction;
2. enforces document, atom, tag, and lineage references with foreign keys;
3. fully reconstructs domain models rather than returning partial records;
4. uses WAL mode for safe local concurrency; and
5. keeps generated Temporal History state separate and rebuildable.

The repository protocol remains the domain boundary. SQLite is an adapter, not a
dependency imported into ingestion, tagging, or temporal projection logic.

## Alternatives

### Neo4j immediately

Neo4j provides expressive graph traversal and visualization, but requires another
running service, resource allocation, credentials, network configuration, backups,
and integration testing. Current queries do not yet demonstrate that need.

### In-memory storage only

This keeps tests fast but loses all data at process exit and cannot support real
ingestion or model evaluation.

### Split canonical data between specialized stores

Separate document, graph, and vector databases may optimize individual workloads,
but introduce synchronization and recovery problems before scale requires them.

## Consequences

- A project can be backed up or moved by safely copying one database file while no
  write transaction is active.
- The laptop and Mac can run the same adapter without another server process.
- SQLite files contain source text and derived knowledge, so they stay outside Git.
- Retrieval indexes can be added alongside canonical tables without changing the
  domain contract.
- Neo4j remains an available future adapter if measured traversal requirements
  exceed SQLite's practical limits.

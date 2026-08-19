# ADR-0004: Laptop canonical storage with on-demand Mac inference

- Status: Accepted as a temporary deployment; sequencing amended by ADR-0005
- Date: 2026-08-19

## Context

The project can use a shared Mac Mini with Ollama, but hosted canonical storage has not
been selected. The user does not want temporary infrastructure that will be discarded
when an online database is introduced. The Mac must remain idle when the laptop is off.

## Decision

1. SQLite and source files remain on the laptop.
2. Ingestion is started and orchestrated on the laptop.
3. The Mac is an optional, on-demand Ollama inference dependency for atom-level tag
   proposals.
4. Ollama is reached through an SSH local-forward tunnel and is not exposed publicly.
5. The core depends on a `TagProposer` protocol, not on Ollama or Mac-specific networking.
6. AI enrichment is an explicit second stage after raw persistence, as decided in
   ADR-0005.
7. No job queue, background service, or persistent ingestion data is added to the Mac in
   this phase.

## Alternatives

### Store a work queue on the Mac

This would allow processing after the laptop shuts down, but duplicates data and creates
temporary synchronization, cleanup, and recovery behavior. It is deferred until hosted
storage is available.

### Run the model on the laptop

This removes the network boundary but does not use the available Mac compute and may be
slower or compete with development work.

### Wait for hosted storage

This is operationally cleaner but delays testing the model-assisted ingestion contract.

## Consequences

- Turning off the laptop or tunnel stops ingestion; the Mac then remains idle.
- Atom content crosses an encrypted tunnel but is processed on a friend-administered Mac.
- The model can be changed without changing raw ingestion or repository contracts.
- Moving from SQLite to hosted PostgreSQL later does not require redesigning tag proposal.
- Long documents make one synchronous model request per atom for now. Resumable background
  jobs remain a later milestone after online storage is chosen.

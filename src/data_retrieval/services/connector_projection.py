from __future__ import annotations

from collections.abc import Callable
from datetime import datetime

from data_retrieval.connectors.contracts import (
    Record,
    SyncBatch,
    SyncBatchAcknowledgement,
    SyncItemType,
)
from data_retrieval.connectors.projection import (
    ConnectorRecordProjection,
    evidence_id,
    projection_metadata,
    projection_source,
    scope_namespace,
)
from data_retrieval.domain.models import (
    AtomLink,
    AtomLinkRelation,
    IngestionBundle,
    PayloadModality,
    utc_now,
)
from data_retrieval.services.ingestion import IngestService
from data_retrieval.storage.repository import CortexRepository


class ConnectorProjectionService:
    """Project durable connector envelopes into Cortex-owned retrieval objects."""

    def __init__(
        self,
        repository: CortexRepository,
        *,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.repository = repository
        self.clock = clock
        self.ingestion = IngestService(repository)

    def project_batch(
        self, batch: SyncBatch, acknowledgement: SyncBatchAcknowledgement
    ) -> None:
        failures = {failure.key for failure in acknowledgement.failures}
        for record in batch.records:
            key = (
                SyncItemType.RECORD,
                record.ref.external_id,
                record.ref.external_version or "",
            )
            if key not in failures:
                self.project_record(record)
        for tombstone in batch.tombstones:
            key = (
                SyncItemType.TOMBSTONE,
                tombstone.record.external_id,
                tombstone.tombstone_version,
            )
            if key not in failures:
                self.repository.apply_connector_tombstone_projection(
                    tombstone=tombstone, applied_at=self.clock()
                )

    def project_record(self, record: Record) -> ConnectorRecordProjection:
        existing = self.repository.get_connector_record_projection(record.ref)
        if existing is not None:
            return existing
        if record.payload.inline is None:
            raise ValueError(
                "referenced connector payload projection is not supported; "
                "submit inline content"
            )

        namespace = scope_namespace(record.scope)
        result = self.ingestion.ingest_text(
            namespace=namespace,
            source=projection_source(record.ref),
            text=record.payload.inline,
            explicit_tags=record.explicit_tags,
            occurred_at=record.occurred_at or record.observed_at,
            metadata=projection_metadata(record),
            payload_modality=PayloadModality(record.modality.value),
        )
        predecessor = self.repository.get_connector_record_predecessor(record.ref)
        predecessor_projection = (
            self.repository.get_connector_record_projection(predecessor)
            if predecessor is not None
            else None
        )
        if predecessor_projection is not None:
            atoms = self.repository.get_atoms_for_document(result.document_id)
            links = tuple(
                AtomLink(
                    from_atom_id=result.atom_ids[
                        min(index, len(result.atom_ids) - 1)
                    ],
                    to_atom_id=old_atom_id,
                    relation=AtomLinkRelation.SUPERSEDES,
                    evidence_sources=("connector_external_version",),
                    metadata={
                        "source_system": record.ref.source.source_system,
                        "source_instance": record.ref.source.source_instance,
                        "external_id": record.ref.external_id,
                    },
                )
                for index, old_atom_id in enumerate(predecessor_projection.atom_ids)
            )
            document = self.repository.get_document(result.document_id)
            assert document is not None
            self.repository.persist_ingestion(
                IngestionBundle(
                    document=document,
                    atoms=atoms,
                    tags=(),
                    atom_tags=(),
                    atom_links=links,
                )
            )

        projection = ConnectorRecordProjection(
            record=record.ref,
            namespace=namespace,
            document_id=result.document_id,
            atom_ids=result.atom_ids,
            evidence_ids=tuple(evidence_id(atom_id) for atom_id in result.atom_ids),
            projected_at=self.clock(),
        )
        return self.repository.store_connector_record_projection(projection)

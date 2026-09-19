from __future__ import annotations

from collections.abc import Mapping
from types import TracebackType
from typing import Any, Self
from urllib.parse import quote

import httpx

from data_retrieval.connectors.codec import (
    context_pack_from_mapping,
    outcome_from_mapping,
    outcome_to_mapping,
    query_to_mapping,
    source_from_mapping,
    source_to_mapping,
    sync_batch_acknowledgement_from_mapping,
    sync_batch_to_mapping,
    sync_commit_acknowledgement_from_mapping,
    sync_run_from_mapping,
)
from data_retrieval.connectors.contracts import (
    ContextPack,
    Outcome,
    Query,
    Record,
    Relation,
    Source,
    SourceRef,
    SyncBatch,
    SyncBatchAcknowledgement,
    SyncCommitAcknowledgement,
    SyncRun,
    Tombstone,
)


class CortexApiError(RuntimeError):
    """Structured transport or server failure; the SDK never retries automatically."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        retryable: bool,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.retry_after = retry_after


class CortexClient:
    """Thin synchronous client for Cortex's versioned application API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.strip():
            raise ValueError("base_url cannot be empty")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._client = httpx.Client(
            base_url=f"{base_url.rstrip('/')}/",
            timeout=timeout,
            transport=transport,
            headers={"Accept": "application/json"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def register_source(self, source: Source) -> Source:
        payload = self._request("POST", "v1/sources", json=source_to_mapping(source))
        return source_from_mapping(payload)

    def get_source(self, source: SourceRef) -> Source | None:
        path = (
            f"v1/sources/{quote(source.source_system, safe='')}/"
            f"{quote(source.source_instance, safe='')}"
        )
        try:
            payload = self._request("GET", path)
        except CortexApiError as error:
            if error.status_code == 404:
                return None
            raise
        return source_from_mapping(payload)

    def submit_batch(self, batch: SyncBatch) -> SyncBatchAcknowledgement:
        run_id = quote(batch.run.request_id, safe="")
        payload = self._request(
            "POST",
            f"v1/sync-runs/{run_id}/batches",
            json=sync_batch_to_mapping(batch),
        )
        return sync_batch_acknowledgement_from_mapping(payload)

    def commit_sync(
        self, *, request_id: str, run_request_id: str
    ) -> SyncCommitAcknowledgement:
        if not request_id.strip():
            raise ValueError("request_id cannot be empty")
        run_id = quote(run_request_id, safe="")
        payload = self._request(
            "POST",
            f"v1/sync-runs/{run_id}:commit",
            json={"request_id": request_id},
        )
        return sync_commit_acknowledgement_from_mapping(payload)

    def get_sync_run(self, request_id: str) -> SyncRun | None:
        run_id = quote(request_id, safe="")
        try:
            payload = self._request("GET", f"v1/sync-runs/{run_id}")
        except CortexApiError as error:
            if error.status_code == 404:
                return None
            raise
        return sync_run_from_mapping(payload)

    def query(self, query: Query) -> ContextPack:
        payload = self._request("POST", "v1/queries", json=query_to_mapping(query))
        return context_pack_from_mapping(payload)

    def report_outcome(self, outcome: Outcome) -> Outcome:
        payload = self._request(
            "POST", "v1/outcomes", json=outcome_to_mapping(outcome)
        )
        return outcome_from_mapping(payload)

    def sync(self, run: SyncRun, *, start_sequence: int = 0) -> SyncSession:
        return SyncSession(self, run, start_sequence=start_sequence)

    def _request(
        self,
        method: str,
        path: str,
        *,
        json: Mapping[str, object] | None = None,
    ) -> Mapping[str, Any]:
        try:
            response = self._client.request(method, path, json=json)
        except httpx.TransportError as error:
            raise CortexApiError(
                f"Cortex request failed before a response was received: {error}",
                status_code=None,
                retryable=True,
            ) from error
        if response.is_error:
            detail = _error_detail(response)
            raise CortexApiError(
                detail,
                status_code=response.status_code,
                retryable=response.status_code in {408, 425, 429} or response.status_code >= 500,
                retry_after=response.headers.get("Retry-After"),
            )
        try:
            payload = response.json()
        except ValueError as error:
            raise CortexApiError(
                "Cortex returned a non-JSON success response",
                status_code=response.status_code,
                retryable=False,
            ) from error
        if not isinstance(payload, Mapping):
            raise CortexApiError(
                "Cortex returned a non-object success response",
                status_code=response.status_code,
                retryable=False,
            )
        return payload


class SyncSession:
    """Explicit batch builder; leaving the context never commits implicitly."""

    def __init__(
        self, client: CortexClient, run: SyncRun, *, start_sequence: int = 0
    ) -> None:
        if start_sequence < 0:
            raise ValueError("start_sequence must be non-negative")
        self.client = client
        self.run = run
        self.next_sequence = start_sequence
        self.committed = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self,
        _exception_type: type[BaseException] | None,
        _exception: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        return None

    def submit(
        self,
        *,
        batch_id: str,
        records: tuple[Record, ...] = (),
        relations: tuple[Relation, ...] = (),
        tombstones: tuple[Tombstone, ...] = (),
    ) -> SyncBatchAcknowledgement:
        if self.committed:
            raise ValueError("sync session is already committed")
        batch = SyncBatch(
            batch_id=batch_id,
            sequence=self.next_sequence,
            run=self.run,
            records=records,
            relations=relations,
            tombstones=tombstones,
        )
        acknowledgement = self.client.submit_batch(batch)
        self.next_sequence += 1
        return acknowledgement

    def submit_batch(self, batch: SyncBatch) -> SyncBatchAcknowledgement:
        if self.committed:
            raise ValueError("sync session is already committed")
        if batch.run != self.run:
            raise ValueError("batch run does not match sync session")
        if batch.sequence != self.next_sequence:
            raise ValueError("batch sequence does not match sync session next_sequence")
        acknowledgement = self.client.submit_batch(batch)
        self.next_sequence += 1
        return acknowledgement

    def commit(self, *, request_id: str) -> SyncCommitAcknowledgement:
        if self.committed:
            raise ValueError("sync session is already committed")
        acknowledgement = self.client.commit_sync(
            request_id=request_id,
            run_request_id=self.run.request_id,
        )
        self.committed = True
        return acknowledgement


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return f"Cortex returned HTTP {response.status_code}"
    if isinstance(payload, Mapping) and isinstance(payload.get("detail"), str):
        return str(payload["detail"])
    return f"Cortex returned HTTP {response.status_code}"

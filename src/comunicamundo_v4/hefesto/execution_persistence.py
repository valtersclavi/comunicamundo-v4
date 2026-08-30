"""Append-only persistence for Hefesto V2 execution telemetry."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from typing import Protocol
from uuid import UUID

from google.api_core.exceptions import Aborted, AlreadyExists, PreconditionFailed
from google.cloud.firestore_v1._helpers import LastUpdateOption

from .execution_contracts import LLMAttemptRecord, WorkUnit


LLM_CALLS_COLLECTION = "hefesto_llm_calls"
EXECUTION_RUNS_COLLECTION = "hefesto_execution_runs"
WORK_UNITS_SUBCOLLECTION = "work_units"


class DocumentSnapshot(Protocol):
    @property
    def exists(self) -> bool: ...

    def to_dict(self) -> dict | None: ...

    @property
    def update_time(self) -> object: ...


class DocumentReference(Protocol):
    def create(self, data: dict) -> object: ...

    def get(self) -> DocumentSnapshot: ...

    def collection(self, name: str) -> "CollectionReference": ...

    def update(self, data: dict, option: object | None = None) -> object: ...


class CollectionReference(Protocol):
    def document(self, document_id: str) -> DocumentReference: ...


class FirestoreClient(Protocol):
    def collection(self, name: str) -> CollectionReference: ...


class FirestoreUsageLedger:
    """Persists one immutable document per provider attempt."""

    def __init__(self, firestore: FirestoreClient, *, collection: str = LLM_CALLS_COLLECTION) -> None:
        self._firestore = firestore
        self._collection = collection

    async def append(self, record: LLMAttemptRecord) -> None:
        await asyncio.to_thread(self._append_sync, record)

    def _append_sync(self, record: LLMAttemptRecord) -> None:
        reference = self._firestore.collection(self._collection).document(str(record.attempt_id))
        payload = _firestore_payload(record.model_dump(mode="python"))
        try:
            reference.create(payload)
        except AlreadyExists:
            snapshot = reference.get()
            existing = snapshot.to_dict() if snapshot.exists else None
            if existing != payload:
                raise ValueError(f"conflicting immutable LLM attempt {record.attempt_id}")


class WorkUnitConflict(RuntimeError):
    pass


WorkTransition = Callable[[WorkUnit], WorkUnit]


class FirestoreWorkRepository:
    """Creates immutable identities and applies state changes with optimistic CAS."""

    def __init__(self, firestore: FirestoreClient) -> None:
        self._firestore = firestore

    async def create(self, unit: WorkUnit) -> WorkUnit:
        return await asyncio.to_thread(self._create_sync, unit)

    async def get(self, execution_run_id: UUID, work_unit_id: str) -> WorkUnit | None:
        return await asyncio.to_thread(self._get_sync, execution_run_id, work_unit_id)

    async def transition(
        self,
        execution_run_id: UUID,
        work_unit_id: str,
        change: WorkTransition,
    ) -> WorkUnit:
        return await asyncio.to_thread(self._transition_sync, execution_run_id, work_unit_id, change)

    def _reference(self, execution_run_id: UUID, work_unit_id: str) -> DocumentReference:
        run = self._firestore.collection(EXECUTION_RUNS_COLLECTION).document(str(execution_run_id))
        return run.collection(WORK_UNITS_SUBCOLLECTION).document(work_unit_id)

    def _create_sync(self, unit: WorkUnit) -> WorkUnit:
        reference = self._reference(unit.execution_run_id, unit.work_unit_id)
        payload = _firestore_payload(unit.model_dump(mode="python"))
        try:
            reference.create(payload)
        except AlreadyExists:
            existing = self._snapshot_unit(reference.get())
            if existing != unit:
                raise ValueError(f"conflicting immutable work unit {unit.work_unit_id}")
        return unit

    def _get_sync(self, execution_run_id: UUID, work_unit_id: str) -> WorkUnit | None:
        return self._snapshot_unit(self._reference(execution_run_id, work_unit_id).get())

    def _transition_sync(
        self,
        execution_run_id: UUID,
        work_unit_id: str,
        change: WorkTransition,
    ) -> WorkUnit:
        reference = self._reference(execution_run_id, work_unit_id)
        snapshot = reference.get()
        current = self._snapshot_unit(snapshot)
        if current is None:
            raise KeyError(f"unknown work unit {work_unit_id}")
        updated = change(current)
        if (
            updated.work_unit_id != current.work_unit_id
            or updated.execution_run_id != current.execution_run_id
            or updated.project_id != current.project_id
        ):
            raise ValueError("work-unit identity is immutable")
        try:
            reference.update(
                _firestore_payload(updated.model_dump(mode="python")),
                option=LastUpdateOption(snapshot.update_time),
            )
        except (Aborted, PreconditionFailed) as error:
            raise WorkUnitConflict(f"concurrent update for {work_unit_id}") from error
        return updated

    @staticmethod
    def _snapshot_unit(snapshot: DocumentSnapshot) -> WorkUnit | None:
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        return None if data is None else WorkUnit.model_validate(data)


def _firestore_payload(value):
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime):
        return value
    if isinstance(value, dict):
        return {key: _firestore_payload(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_firestore_payload(item) for item in value]
    return value

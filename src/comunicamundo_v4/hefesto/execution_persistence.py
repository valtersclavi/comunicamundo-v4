"""Append-only persistence for Hefesto V2 execution telemetry."""
from __future__ import annotations

import asyncio
from typing import Protocol

from google.api_core.exceptions import AlreadyExists

from .execution_contracts import LLMAttemptRecord


LLM_CALLS_COLLECTION = "hefesto_llm_calls"


class DocumentSnapshot(Protocol):
    @property
    def exists(self) -> bool: ...

    def to_dict(self) -> dict | None: ...


class DocumentReference(Protocol):
    def create(self, data: dict) -> object: ...

    def get(self) -> DocumentSnapshot: ...


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
        payload = record.model_dump(mode="json")
        try:
            reference.create(payload)
        except AlreadyExists:
            snapshot = reference.get()
            existing = snapshot.to_dict() if snapshot.exists else None
            if existing != payload:
                raise ValueError(f"conflicting immutable LLM attempt {record.attempt_id}")

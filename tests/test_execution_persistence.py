from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from google.api_core.exceptions import AlreadyExists

from comunicamundo_v4.hefesto.execution_contracts import (
    LLMAttemptRecord,
    LLMCallStatus,
    LLMUsage,
)
from comunicamundo_v4.hefesto.execution_persistence import (
    LLM_CALLS_COLLECTION,
    FirestoreUsageLedger,
)


class Snapshot:
    def __init__(self, data) -> None:
        self.data = data

    @property
    def exists(self) -> bool:
        return self.data is not None

    def to_dict(self):
        return self.data


class Document:
    def __init__(self, store, key) -> None:
        self.store = store
        self.key = key

    def create(self, data) -> None:
        if self.key in self.store:
            raise AlreadyExists("exists")
        self.store[self.key] = data

    def get(self):
        return Snapshot(self.store.get(self.key))


class Collection:
    def __init__(self, store, name) -> None:
        self.store = store
        self.name = name

    def document(self, document_id):
        return Document(self.store, (self.name, document_id))


class Firestore:
    def __init__(self) -> None:
        self.store = {}

    def collection(self, name):
        return Collection(self.store, name)


def _record() -> LLMAttemptRecord:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    return LLMAttemptRecord(
        call_id=uuid4(),
        execution_run_id=uuid4(),
        project_id=uuid4(),
        work_unit_id="dossier:1",
        task_type="source_dossier",
        prompt_version="v1",
        model="gemini-2.5-flash",
        location="global",
        attempt=1,
        status=LLMCallStatus.SUCCEEDED,
        queued_at=now,
        started_at=now,
        finished_at=now + timedelta(seconds=1),
        queue_wait_ms=0,
        latency_ms=1_000,
        estimated_input_tokens=100,
        reserved_capacity_tokens=200,
        usage=LLMUsage(prompt_tokens=100, output_tokens=10, total_tokens=110),
    )


@pytest.mark.asyncio
async def test_firestore_usage_ledger_is_append_only_and_idempotent() -> None:
    firestore = Firestore()
    ledger = FirestoreUsageLedger(firestore)
    record = _record()

    await ledger.append(record)
    await ledger.append(record)

    assert len(firestore.store) == 1
    assert firestore.store[(LLM_CALLS_COLLECTION, str(record.attempt_id))]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_firestore_usage_ledger_rejects_conflicting_duplicate() -> None:
    firestore = Firestore()
    ledger = FirestoreUsageLedger(firestore)
    record = _record()
    await ledger.append(record)
    conflicting = record.model_copy(update={"latency_ms": 2_000})

    with pytest.raises(ValueError, match="conflicting immutable"):
        await ledger.append(conflicting)

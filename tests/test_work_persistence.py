from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from google.api_core.exceptions import AlreadyExists, PreconditionFailed

from comunicamundo_v4.hefesto.execution_contracts import WorkUnit, WorkUnitStatus, text_sha256, work_unit_key
from comunicamundo_v4.hefesto.execution_persistence import FirestoreWorkRepository, WorkUnitConflict


class Snapshot:
    def __init__(self, data, version) -> None:
        self.data = data
        self.update_time = version

    @property
    def exists(self) -> bool:
        return self.data is not None

    def to_dict(self):
        return None if self.data is None else dict(self.data)


class Document:
    def __init__(self, firestore, key) -> None:
        self.firestore = firestore
        self.key = key

    def create(self, data) -> None:
        if self.key in self.firestore.store:
            raise AlreadyExists("exists")
        self.firestore.store[self.key] = dict(data)
        self.firestore.versions[self.key] = 1

    def get(self):
        return Snapshot(self.firestore.store.get(self.key), self.firestore.versions.get(self.key))

    def update(self, data, option=None) -> None:
        if self.firestore.force_conflict:
            self.firestore.versions[self.key] += 1
            self.firestore.force_conflict = False
        if option._last_update_time != self.firestore.versions.get(self.key):
            raise PreconditionFailed("changed")
        self.firestore.store[self.key].update(data)
        self.firestore.versions[self.key] += 1

    def collection(self, name):
        return Collection(self.firestore, (*self.key, name))


class Collection:
    def __init__(self, firestore, prefix) -> None:
        self.firestore = firestore
        self.prefix = prefix if isinstance(prefix, tuple) else (prefix,)

    def document(self, document_id):
        return Document(self.firestore, (*self.prefix, document_id))


class Firestore:
    def __init__(self) -> None:
        self.store = {}
        self.versions = {}
        self.force_conflict = False

    def collection(self, name):
        return Collection(self, name)


def _unit() -> WorkUnit:
    run_id = uuid4()
    input_hash = text_sha256("input")
    config_hash = text_sha256("config")
    unit_id = work_unit_key(
        engine_version="v2",
        unit_type="source_dossier",
        input_sha256s=[input_hash],
        prompt_version="v1",
        model="gemini-2.5-flash",
        model_config_sha256=config_hash,
        cache_scope=f"run:{run_id}",
    )
    return WorkUnit(
        work_unit_id=unit_id,
        execution_run_id=run_id,
        project_id=uuid4(),
        engine_version="v2",
        unit_type="source_dossier",
        input_uris=["gs://bucket/input.json"],
        input_sha256s=[input_hash],
        prompt_version="v1",
        model="gemini-2.5-flash",
        model_config_sha256=config_hash,
        cache_scope=f"run:{run_id}",
        reserved_capacity_tokens=100,
    )


@pytest.mark.asyncio
async def test_work_repository_create_is_idempotent_and_transition_is_atomic() -> None:
    firestore = Firestore()
    repository = FirestoreWorkRepository(firestore)
    unit = _unit()
    await repository.create(unit)
    await repository.create(unit)
    now = datetime(2026, 8, 30, tzinfo=UTC)

    leased = await repository.transition(
        unit.execution_run_id,
        unit.work_unit_id,
        lambda current: current.lease(owner="worker", now=now, ttl=timedelta(minutes=1)),
    )
    loaded = await repository.get(unit.execution_run_id, unit.work_unit_id)

    assert leased.status is WorkUnitStatus.LEASED
    assert loaded == leased


@pytest.mark.asyncio
async def test_work_repository_rejects_lost_update_instead_of_overwriting() -> None:
    firestore = Firestore()
    repository = FirestoreWorkRepository(firestore)
    unit = _unit()
    await repository.create(unit)
    firestore.force_conflict = True
    now = datetime(2026, 8, 30, tzinfo=UTC)

    with pytest.raises(WorkUnitConflict):
        await repository.transition(
            unit.execution_run_id,
            unit.work_unit_id,
            lambda current: current.lease(owner="worker", now=now, ttl=timedelta(minutes=1)),
        )

    loaded = await repository.get(unit.execution_run_id, unit.work_unit_id)
    assert loaded.status is WorkUnitStatus.PENDING


@pytest.mark.asyncio
async def test_work_repository_rejects_identity_change() -> None:
    firestore = Firestore()
    repository = FirestoreWorkRepository(firestore)
    unit = _unit()
    await repository.create(unit)

    with pytest.raises(ValueError, match="identity is immutable"):
        await repository.transition(
            unit.execution_run_id,
            unit.work_unit_id,
            lambda current: current.model_copy(update={"project_id": uuid4()}),
        )

from __future__ import annotations

from uuid import uuid4

import pytest
from google.api_core.exceptions import AlreadyExists, PreconditionFailed

from comunicamundo_v4.hefesto.contracts import NormalizedSource
from comunicamundo_v4.hefesto.digestion_contracts import (
    ArtifactPointer,
    DigestionArtifactRefs,
    DigestionExecutionRun,
    DigestionRunState,
)
from comunicamundo_v4.hefesto.digestion_persistence import GcsDigestionArtifactStore
from comunicamundo_v4.hefesto.source_digestion import SourceDigestionPlanner


class Blob:
    def __init__(self) -> None:
        self.data: bytes | None = None

    def upload_from_string(self, data, **kwargs) -> None:
        if kwargs.get("if_generation_match") == 0 and self.data is not None:
            raise PreconditionFailed("exists")
        self.data = data if isinstance(data, bytes) else data.encode()

    def download_as_bytes(self) -> bytes:
        assert self.data is not None
        return self.data


class Bucket:
    def __init__(self) -> None:
        self.blobs = {}

    def blob(self, name):
        return self.blobs.setdefault(name, Blob())


class Storage:
    def __init__(self) -> None:
        self.value = Bucket()

    def bucket(self, name):
        return self.value


class Snapshot:
    def __init__(self, data, version) -> None:
        self.data = data
        self.update_time = version

    @property
    def exists(self):
        return self.data is not None

    def to_dict(self):
        return None if self.data is None else dict(self.data)


class Document:
    def __init__(self, database, key):
        self.database = database
        self.key = key

    def create(self, data):
        if self.key in self.database.store:
            raise AlreadyExists("exists")
        self.database.store[self.key] = dict(data)
        self.database.versions[self.key] = 1

    def get(self):
        return Snapshot(self.database.store.get(self.key), self.database.versions.get(self.key))

    def update(self, data, option=None):
        if self.database.conflict:
            self.database.versions[self.key] += 1
            self.database.conflict = False
        if option._last_update_time != self.database.versions[self.key]:
            raise PreconditionFailed("changed")
        self.database.store[self.key].update(data)
        self.database.versions[self.key] += 1


class Collection:
    def __init__(self, database, name):
        self.database = database
        self.name = name

    def document(self, key):
        return Document(self.database, (self.name, key))


class Firestore:
    def __init__(self):
        self.store = {}
        self.versions = {}
        self.conflict = False

    def collection(self, name):
        return Collection(self, name)


def _plan():
    project_id = uuid4()
    source = NormalizedSource(source_id=uuid4(), text="Fonte normalizada.", language="pt-BR")
    return SourceDigestionPlanner().plan(project_id=project_id, sources=[source])


def _run(plan, pointer):
    return DigestionExecutionRun(
        execution_run_id=plan.execution_run_id,
        project_id=plan.project_id,
        source_fingerprint=plan.source_fingerprint,
        brief_fingerprint="0" * 64,
        state=DigestionRunState.PLANNED,
        source_count=1,
        planned_model_calls=plan.planned_model_calls,
        artifacts=DigestionArtifactRefs(request=pointer, plan=pointer),
    )


@pytest.mark.asyncio
async def test_gcs_artifact_is_immutable_idempotent_and_hash_checked() -> None:
    storage = Storage()
    store = GcsDigestionArtifactStore(storage, bucket_name="bucket", root_prefix="root")
    plan = _plan()

    first = await store.persist_plan(plan)
    second = await store.persist_plan(plan)
    loaded = await store.load(first, type(plan))

    assert first == second
    assert loaded == plan
    storage.value.blob(first.uri.removeprefix("gs://bucket/")).data = b"tampered"
    with pytest.raises(ValueError, match="integrity"):
        await store.load(first, type(plan))


@pytest.mark.asyncio
async def test_gcs_artifact_rejects_conflicting_replay_at_the_same_path() -> None:
    storage = Storage()
    store = GcsDigestionArtifactStore(storage, bucket_name="bucket", root_prefix="root")
    plan = _plan()
    await store.persist_plan(plan)
    changed = plan.model_copy(update={"planned_model_calls": plan.planned_model_calls + 1})

    with pytest.raises(ValueError, match="conflicting immutable"):
        await store.persist_plan(changed)


def test_ready_run_requires_all_dossiers_matrix_understanding_and_calls() -> None:
    plan = _plan()
    pointer = ArtifactPointer(uri="gs://bucket/plan.json", sha256="0" * 64)
    run = _run(plan, pointer)

    with pytest.raises(ValueError, match="one dossier per source"):
        run.model_copy(update={"state": DigestionRunState.UNDERSTANDING_READY}, deep=True).__class__.model_validate(
            {**run.model_dump(), "state": DigestionRunState.UNDERSTANDING_READY}
        )

"""Immutable production artifacts and CAS run state for Hefesto V2."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from hashlib import sha256
from typing import Any, Protocol, TypeVar
from uuid import UUID

from google.api_core.exceptions import Aborted, AlreadyExists, PreconditionFailed
from google.cloud.firestore_v1._helpers import LastUpdateOption
from pydantic import BaseModel

from .contracts import EditorialReview, FinalArticle, NarrativePlan
from .digestion_contracts import ArtifactPointer
from .execution_persistence import _firestore_payload
from .production_v2_contracts import (
    ArticleDraftV2,
    ProductionEvidenceBundle,
    ProductionExecutionRunV2,
    ProductionRecordV2,
    SubjectResearchBundleProposal,
)


PRODUCTION_V2_RUNS_COLLECTION = "hefesto_production_v2_runs"
ModelT = TypeVar("ModelT", bound=BaseModel)


class ProductionV2ArtifactStore(Protocol):
    async def persist_evidence_bundle(self, run_id: UUID, project_id: UUID, value: ProductionEvidenceBundle) -> ArtifactPointer: ...
    async def persist_research(self, run_id: UUID, project_id: UUID, value: SubjectResearchBundleProposal) -> ArtifactPointer: ...
    async def persist_architecture(self, run_id: UUID, project_id: UUID, value: NarrativePlan) -> ArtifactPointer: ...
    async def persist_draft(self, run_id: UUID, project_id: UUID, value: ArticleDraftV2, *, round_index: int) -> ArtifactPointer: ...
    async def persist_review(self, run_id: UUID, project_id: UUID, value: EditorialReview, *, round_index: int) -> ArtifactPointer: ...
    async def persist_final_article(self, run_id: UUID, project_id: UUID, value: FinalArticle) -> ArtifactPointer: ...
    async def persist_production_record(self, run_id: UUID, project_id: UUID, value: ProductionRecordV2) -> ArtifactPointer: ...
    async def find_evidence_bundle(self, run_id: UUID, project_id: UUID) -> tuple[ArtifactPointer, ProductionEvidenceBundle] | None: ...
    async def find_final_article(self, run_id: UUID, project_id: UUID) -> tuple[ArtifactPointer, FinalArticle] | None: ...
    async def find_production_record(self, run_id: UUID, project_id: UUID) -> tuple[ArtifactPointer, ProductionRecordV2] | None: ...
    async def load(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT: ...


class GcsProductionV2ArtifactStore:
    def __init__(self, storage_client: Any, *, bucket_name: str, root_prefix: str) -> None:
        self._bucket = storage_client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._root = root_prefix.strip("/")
        if not self._root:
            raise ValueError("root prefix cannot be empty")

    async def persist_evidence_bundle(self, run_id, project_id, value):
        return await self._persist(run_id, project_id, "evidence-bundle.json", value)

    async def persist_research(self, run_id, project_id, value):
        return await self._persist(run_id, project_id, "research.json", value)

    async def persist_architecture(self, run_id, project_id, value):
        return await self._persist(run_id, project_id, "architecture.json", value)

    async def persist_draft(self, run_id, project_id, value, *, round_index):
        return await self._persist(run_id, project_id, f"drafts/{round_index:02d}.json", value)

    async def persist_review(self, run_id, project_id, value, *, round_index):
        return await self._persist(run_id, project_id, f"reviews/{round_index:02d}.json", value)

    async def persist_final_article(self, run_id, project_id, value):
        return await self._persist(run_id, project_id, "final-article.json", value)

    async def persist_production_record(self, run_id, project_id, value):
        return await self._persist(run_id, project_id, "production-record.json", value)

    async def find_evidence_bundle(self, run_id, project_id):
        return await self._find(run_id, project_id, "evidence-bundle.json", ProductionEvidenceBundle)

    async def find_final_article(self, run_id, project_id):
        return await self._find(run_id, project_id, "final-article.json", FinalArticle)

    async def find_production_record(self, run_id, project_id):
        return await self._find(run_id, project_id, "production-record.json", ProductionRecordV2)

    async def load(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT:
        return await asyncio.to_thread(self._load_sync, pointer, schema)

    async def _persist(self, run_id: UUID, project_id: UUID, path: str, value: BaseModel) -> ArtifactPointer:
        return await asyncio.to_thread(self._persist_sync, run_id, project_id, path, value)

    async def _find(self, run_id: UUID, project_id: UUID, path: str, schema: type[ModelT]):
        return await asyncio.to_thread(self._find_sync, run_id, project_id, path, schema)

    def _name(self, run_id: UUID, project_id: UUID, path: str) -> str:
        return f"{self._root}/projects/{project_id}/executions/{run_id}/production/{path}"

    def _persist_sync(self, run_id: UUID, project_id: UUID, path: str, value: BaseModel) -> ArtifactPointer:
        data = value.model_dump_json().encode("utf-8")
        digest = sha256(data).hexdigest()
        name = self._name(run_id, project_id, path)
        blob = self._bucket.blob(name)
        try:
            blob.upload_from_string(data, content_type="application/json", if_generation_match=0)
        except (PreconditionFailed, AlreadyExists):
            if blob.download_as_bytes() != data:
                raise ValueError(f"conflicting immutable production artifact: {path}")
        return ArtifactPointer(uri=f"gs://{self._bucket_name}/{name}", sha256=digest)

    def _load_sync(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT:
        prefix = f"gs://{self._bucket_name}/"
        if not pointer.uri.startswith(prefix):
            raise ValueError("production artifact belongs to another bucket")
        data = self._bucket.blob(pointer.uri.removeprefix(prefix)).download_as_bytes()
        if sha256(data).hexdigest() != pointer.sha256:
            raise ValueError("production artifact integrity failure")
        return schema.model_validate_json(data)

    def _find_sync(self, run_id: UUID, project_id: UUID, path: str, schema: type[ModelT]):
        name = self._name(run_id, project_id, path)
        blob = self._bucket.blob(name)
        if not blob.exists():
            return None
        data = blob.download_as_bytes()
        pointer = ArtifactPointer(
            uri=f"gs://{self._bucket_name}/{name}",
            sha256=sha256(data).hexdigest(),
        )
        return pointer, schema.model_validate_json(data)


RunTransition = Callable[[ProductionExecutionRunV2], ProductionExecutionRunV2]


class ProductionV2RunRepository(Protocol):
    async def create(self, run: ProductionExecutionRunV2) -> ProductionExecutionRunV2: ...
    async def get(self, run_id: UUID) -> ProductionExecutionRunV2 | None: ...
    async def transition(self, run_id: UUID, change: RunTransition) -> ProductionExecutionRunV2: ...


class FirestoreProductionV2RunRepository:
    def __init__(self, firestore: Any, *, collection: str = PRODUCTION_V2_RUNS_COLLECTION) -> None:
        self._firestore = firestore
        self._collection = collection

    async def create(self, run):
        return await asyncio.to_thread(self._create_sync, run)

    async def get(self, run_id):
        return await asyncio.to_thread(self._get_sync, run_id)

    async def transition(self, run_id, change):
        return await asyncio.to_thread(self._transition_sync, run_id, change)

    def _reference(self, run_id):
        return self._firestore.collection(self._collection).document(str(run_id))

    def _create_sync(self, run):
        reference = self._reference(run.execution_run_id)
        try:
            reference.create(_firestore_payload(run.model_dump(mode="python")))
        except AlreadyExists:
            existing = self._snapshot(reference.get())
            if existing != run:
                raise ValueError(f"conflicting production run {run.execution_run_id}")
        return run

    def _get_sync(self, run_id):
        return self._snapshot(self._reference(run_id).get())

    def _transition_sync(self, run_id, change):
        reference = self._reference(run_id)
        snapshot = reference.get()
        current = self._snapshot(snapshot)
        if current is None:
            raise KeyError(f"unknown production run {run_id}")
        updated = change(current)
        if (
            updated.execution_run_id != current.execution_run_id
            or updated.project_id != current.project_id
            or updated.editorial_project_id != current.editorial_project_id
        ):
            raise ValueError("production run identity is immutable")
        try:
            reference.update(
                _firestore_payload(updated.model_dump(mode="python")),
                option=LastUpdateOption(snapshot.update_time),
            )
        except (Aborted, PreconditionFailed) as error:
            raise RuntimeError(f"concurrent production update for {run_id}") from error
        return updated

    @staticmethod
    def _snapshot(snapshot):
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        return None if data is None else ProductionExecutionRunV2.model_validate(data)

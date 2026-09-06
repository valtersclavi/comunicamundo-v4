"""Durable metadata and immutable GCS artifacts for Hefesto V2 digestion."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import datetime
from hashlib import sha256
from typing import Any, Protocol, TypeVar
from uuid import UUID

from google.api_core.exceptions import AlreadyExists, PreconditionFailed
from pydantic import BaseModel

from .contracts import PlanningRequest
from .digestion_contracts import (
    ArtifactPointer,
    DigestionExecutionRun,
    DigestionPlan,
    EditorialUnderstandingV2,
    EvidenceMatrix,
    SectionDigest,
    SourceDossier,
)


ModelT = TypeVar("ModelT", bound=BaseModel)


class DigestionArtifactStore(Protocol):
    async def persist_request(self, plan: DigestionPlan, request: PlanningRequest) -> ArtifactPointer: ...
    async def persist_plan(self, plan: DigestionPlan) -> ArtifactPointer: ...
    async def persist_section_digest(self, plan: DigestionPlan, digest: SectionDigest) -> ArtifactPointer: ...
    async def persist_source_dossier(self, plan: DigestionPlan, dossier: SourceDossier) -> ArtifactPointer: ...
    async def persist_evidence_matrix(self, plan: DigestionPlan, matrix: EvidenceMatrix) -> ArtifactPointer: ...
    async def persist_understanding(
        self, plan: DigestionPlan, understanding: EditorialUnderstandingV2
    ) -> ArtifactPointer: ...
    async def load(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT: ...


class GcsDigestionArtifactStore:
    """Writes canonical JSON once; a replay must contain exactly the same bytes."""

    def __init__(self, storage_client: Any, *, bucket_name: str, root_prefix: str) -> None:
        self._bucket = storage_client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._root = root_prefix.strip("/")
        if not self._root:
            raise ValueError("root prefix cannot be empty")

    async def persist_request(self, plan: DigestionPlan, request: PlanningRequest) -> ArtifactPointer:
        return await self._persist(plan, "request.json", request)

    async def persist_plan(self, plan: DigestionPlan) -> ArtifactPointer:
        return await self._persist(plan, "plan.json", plan)

    async def persist_section_digest(self, plan: DigestionPlan, digest: SectionDigest) -> ArtifactPointer:
        return await self._persist(plan, f"sections/{digest.section_id}.json", digest)

    async def persist_source_dossier(self, plan: DigestionPlan, dossier: SourceDossier) -> ArtifactPointer:
        return await self._persist(plan, f"dossiers/{dossier.source_ordinal:04d}-{dossier.source_id}.json", dossier)

    async def persist_evidence_matrix(self, plan: DigestionPlan, matrix: EvidenceMatrix) -> ArtifactPointer:
        return await self._persist(plan, "evidence-matrix.json", matrix)

    async def persist_understanding(
        self,
        plan: DigestionPlan,
        understanding: EditorialUnderstandingV2,
    ) -> ArtifactPointer:
        return await self._persist(plan, "editorial-understanding.json", understanding)

    async def load(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT:
        return await asyncio.to_thread(self._load_sync, pointer, schema)

    async def _persist(self, plan: DigestionPlan, relative_path: str, model: BaseModel) -> ArtifactPointer:
        return await asyncio.to_thread(self._persist_sync, plan, relative_path, model)

    def _persist_sync(self, plan: DigestionPlan, relative_path: str, model: BaseModel) -> ArtifactPointer:
        data = model.model_dump_json().encode("utf-8")
        digest = sha256(data).hexdigest()
        name = (
            f"{self._root}/projects/{plan.project_id}/executions/"
            f"{plan.execution_run_id}/digestion/{relative_path}"
        )
        blob = self._bucket.blob(name)
        try:
            blob.upload_from_string(data, content_type="application/json", if_generation_match=0)
        except (PreconditionFailed, AlreadyExists):
            existing = blob.download_as_bytes()
            if existing != data:
                raise ValueError(f"conflicting immutable digestion artifact: {relative_path}")
        return ArtifactPointer(uri=f"gs://{self._bucket_name}/{name}", sha256=digest)

    def _load_sync(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT:
        prefix = f"gs://{self._bucket_name}/"
        if not pointer.uri.startswith(prefix):
            raise ValueError("digestion artifact belongs to another bucket")
        data = self._bucket.blob(pointer.uri.removeprefix(prefix)).download_as_bytes()
        if sha256(data).hexdigest() != pointer.sha256:
            raise ValueError("digestion artifact integrity failure")
        return schema.model_validate_json(data)


RunTransition = Callable[[DigestionExecutionRun], DigestionExecutionRun]


class ExecutionRunRepository(Protocol):
    async def create(self, run: DigestionExecutionRun) -> DigestionExecutionRun: ...
    async def get(self, execution_run_id: UUID) -> DigestionExecutionRun | None: ...
    async def transition(self, execution_run_id: UUID, change: RunTransition) -> DigestionExecutionRun: ...



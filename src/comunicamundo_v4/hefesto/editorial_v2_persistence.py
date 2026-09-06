"""Immutable editorial artifacts and CAS metadata for Hefesto V2 gates."""
from __future__ import annotations

import asyncio
from collections.abc import Callable
from hashlib import sha256
from typing import Any, Protocol, TypeVar
from uuid import UUID

from google.api_core.exceptions import AlreadyExists, PreconditionFailed
from pydantic import BaseModel

from .contracts import EditorialProjectProposal, NarrativeAxisEvidenceReview, NarrativeAxisProposal
from .digestion_contracts import ArtifactPointer
from .editorial_v2_contracts import (
    EditorialProjectRecordV2,
    EditorialV2ExecutionRun,
    NarrativeAxisRecordV2,
)
from .hefesto_tips import HefestoTipTimingResult


ModelT = TypeVar("ModelT", bound=BaseModel)


class EditorialV2ArtifactStore(Protocol):
    async def persist_axis_candidate(
        self, execution_run_id: UUID, project_id: UUID, value: NarrativeAxisProposal
    ) -> ArtifactPointer: ...
    async def persist_axis_review(
        self, execution_run_id: UUID, project_id: UUID, value: NarrativeAxisEvidenceReview
    ) -> ArtifactPointer: ...
    async def persist_axis_record(
        self, execution_run_id: UUID, project_id: UUID, value: NarrativeAxisRecordV2, *, approved: bool
    ) -> ArtifactPointer: ...
    async def persist_project_proposal(
        self, execution_run_id: UUID, project_id: UUID, value: EditorialProjectProposal
    ) -> ArtifactPointer: ...
    async def persist_tip_timing(
        self, execution_run_id: UUID, project_id: UUID, value: HefestoTipTimingResult
    ) -> ArtifactPointer: ...
    async def persist_project_record(
        self, execution_run_id: UUID, project_id: UUID, value: EditorialProjectRecordV2, *, approved: bool
    ) -> ArtifactPointer: ...
    async def find_axis_record(
        self, execution_run_id: UUID, project_id: UUID, *, approved: bool
    ) -> tuple[ArtifactPointer, NarrativeAxisRecordV2] | None: ...
    async def find_project_record(
        self, execution_run_id: UUID, project_id: UUID, *, approved: bool
    ) -> tuple[ArtifactPointer, EditorialProjectRecordV2] | None: ...
    async def load(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT: ...


class GcsEditorialV2ArtifactStore:
    def __init__(self, storage_client: Any, *, bucket_name: str, root_prefix: str) -> None:
        self._bucket = storage_client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._root = root_prefix.strip("/")
        if not self._root:
            raise ValueError("root prefix cannot be empty")

    async def persist_axis_candidate(
        self, execution_run_id: UUID, project_id: UUID, value: NarrativeAxisProposal
    ) -> ArtifactPointer:
        return await self._persist(execution_run_id, project_id, "axis/candidate.json", value)

    async def persist_axis_review(
        self, execution_run_id: UUID, project_id: UUID, value: NarrativeAxisEvidenceReview
    ) -> ArtifactPointer:
        return await self._persist(execution_run_id, project_id, "axis/evidence-review.json", value)

    async def persist_axis_record(
        self,
        execution_run_id: UUID,
        project_id: UUID,
        value: NarrativeAxisRecordV2,
        *,
        approved: bool,
    ) -> ArtifactPointer:
        name = "axis/approved.json" if approved else "axis/proposed.json"
        return await self._persist(execution_run_id, project_id, name, value)

    async def persist_project_proposal(
        self, execution_run_id: UUID, project_id: UUID, value: EditorialProjectProposal
    ) -> ArtifactPointer:
        return await self._persist(execution_run_id, project_id, "project/proposal.json", value)

    async def persist_tip_timing(
        self, execution_run_id: UUID, project_id: UUID, value: HefestoTipTimingResult
    ) -> ArtifactPointer:
        return await self._persist(execution_run_id, project_id, "project/tip-timing.json", value)

    async def persist_project_record(
        self,
        execution_run_id: UUID,
        project_id: UUID,
        value: EditorialProjectRecordV2,
        *,
        approved: bool,
    ) -> ArtifactPointer:
        name = "project/approved.json" if approved else "project/proposed.json"
        return await self._persist(execution_run_id, project_id, name, value)

    async def find_axis_record(
        self, execution_run_id: UUID, project_id: UUID, *, approved: bool
    ) -> tuple[ArtifactPointer, NarrativeAxisRecordV2] | None:
        name = "axis/approved.json" if approved else "axis/proposed.json"
        return await self._find(execution_run_id, project_id, name, NarrativeAxisRecordV2)

    async def find_project_record(
        self, execution_run_id: UUID, project_id: UUID, *, approved: bool
    ) -> tuple[ArtifactPointer, EditorialProjectRecordV2] | None:
        name = "project/approved.json" if approved else "project/proposed.json"
        return await self._find(execution_run_id, project_id, name, EditorialProjectRecordV2)

    async def load(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT:
        return await asyncio.to_thread(self._load_sync, pointer, schema)

    async def _persist(
        self,
        execution_run_id: UUID,
        project_id: UUID,
        relative_path: str,
        model: BaseModel,
    ) -> ArtifactPointer:
        return await asyncio.to_thread(
            self._persist_sync,
            execution_run_id,
            project_id,
            relative_path,
            model,
        )

    async def _find(
        self,
        execution_run_id: UUID,
        project_id: UUID,
        relative_path: str,
        schema: type[ModelT],
    ) -> tuple[ArtifactPointer, ModelT] | None:
        return await asyncio.to_thread(
            self._find_sync,
            execution_run_id,
            project_id,
            relative_path,
            schema,
        )

    def _persist_sync(
        self,
        execution_run_id: UUID,
        project_id: UUID,
        relative_path: str,
        model: BaseModel,
    ) -> ArtifactPointer:
        data = model.model_dump_json().encode("utf-8")
        digest = sha256(data).hexdigest()
        name = (
            f"{self._root}/projects/{project_id}/executions/"
            f"{execution_run_id}/editorial/{relative_path}"
        )
        blob = self._bucket.blob(name)
        try:
            blob.upload_from_string(data, content_type="application/json", if_generation_match=0)
        except (PreconditionFailed, AlreadyExists):
            existing = blob.download_as_bytes()
            if existing != data:
                raise ValueError(f"conflicting immutable editorial artifact: {relative_path}")
        return ArtifactPointer(uri=f"gs://{self._bucket_name}/{name}", sha256=digest)

    def _load_sync(self, pointer: ArtifactPointer, schema: type[ModelT]) -> ModelT:
        prefix = f"gs://{self._bucket_name}/"
        if not pointer.uri.startswith(prefix):
            raise ValueError("editorial artifact belongs to another bucket")
        data = self._bucket.blob(pointer.uri.removeprefix(prefix)).download_as_bytes()
        if sha256(data).hexdigest() != pointer.sha256:
            raise ValueError("editorial artifact integrity failure")
        return schema.model_validate_json(data)

    def _find_sync(
        self,
        execution_run_id: UUID,
        project_id: UUID,
        relative_path: str,
        schema: type[ModelT],
    ) -> tuple[ArtifactPointer, ModelT] | None:
        name = (
            f"{self._root}/projects/{project_id}/executions/"
            f"{execution_run_id}/editorial/{relative_path}"
        )
        blob = self._bucket.blob(name)
        if not blob.exists():
            return None
        data = blob.download_as_bytes()
        pointer = ArtifactPointer(
            uri=f"gs://{self._bucket_name}/{name}",
            sha256=sha256(data).hexdigest(),
        )
        return pointer, schema.model_validate_json(data)


RunTransition = Callable[[EditorialV2ExecutionRun], EditorialV2ExecutionRun]


class EditorialV2RunRepository(Protocol):
    async def create(self, run: EditorialV2ExecutionRun) -> EditorialV2ExecutionRun: ...
    async def get(self, execution_run_id: UUID) -> EditorialV2ExecutionRun | None: ...
    async def transition(self, execution_run_id: UUID, change: RunTransition) -> EditorialV2ExecutionRun: ...



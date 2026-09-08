from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from hashlib import sha256
import json
import os
from typing import Any, Callable, Protocol
from uuid import UUID, uuid5

import google.auth
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage
from fastapi import FastAPI, HTTPException, Response, status

from .contracts import EditorialInputRequest
from .postgres import ProductRepository
from .storage import persist_json_immutable


class Dispatcher(Protocol):
    def run(self, args: list[str]) -> str: ...


class GoogleCloudRunDispatcher:
    def __init__(self, project: str, location: str, job: str) -> None:
        self._url = (
            "https://run.googleapis.com/v2/projects/"
            f"{project}/locations/{location}/jobs/{job}:run"
        )

    def run(self, args: list[str]) -> str:
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        response = AuthorizedSession(credentials).post(
            self._url,
            json={
                "overrides": {
                    "containerOverrides": [{"args": args}],
                    "taskCount": 1,
                    "timeout": "3600s",
                }
            },
            timeout=30,
        )
        response.raise_for_status()
        operation = response.json()
        if not operation.get("name"):
            raise RuntimeError("Cloud Run returned no operation identity")
        return str(operation["name"])


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def _default_dispatcher() -> Dispatcher:
    return GoogleCloudRunDispatcher(
        _required("GOOGLE_CLOUD_PROJECT"),
        os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1"),
        os.environ.get("HEFESTO_JOB_NAME", "hefesto-v4-rebuild"),
    )


def _canonical_request(request: EditorialInputRequest) -> tuple[dict[str, Any], bytes]:
    payload = request.model_dump(mode="json")
    data = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return payload, data


def _execution_id(request: EditorialInputRequest, data: bytes) -> UUID:
    return uuid5(request.project_id, f"initial-plan:{sha256(data).hexdigest()}")


class SubmissionService:
    def __init__(
        self,
        client: storage.Client,
        bucket_name: str,
        dispatcher: Dispatcher,
    ) -> None:
        self._client = client
        self._bucket_name = bucket_name
        self._dispatcher = dispatcher

    def submit(self, request: EditorialInputRequest) -> dict[str, Any]:
        payload, data = _canonical_request(request)
        execution_id = _execution_id(request, data)
        prefix = f"hefesto-product/executions/{execution_id}"
        request_uri = persist_json_immutable(
            self._client,
            bucket_name=self._bucket_name,
            name=f"{prefix}/input-request.json",
            payload=payload,
        )
        dispatch_blob = self._client.bucket(self._bucket_name).blob(
            f"{prefix}/plan-dispatch.json"
        )
        claim = {
            "status": "dispatching",
            "project_id": str(request.project_id),
            "execution_run_id": str(execution_id),
            "request_uri": request_uri,
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            dispatch_blob.upload_from_string(
                json.dumps(claim, ensure_ascii=False, sort_keys=True).encode(),
                content_type="application/json",
                if_generation_match=0,
            )
        except PreconditionFailed:
            existing = json.loads(dispatch_blob.download_as_bytes())
            if existing.get("status") != "failed":
                return existing
        try:
            operation = self._dispatcher.run([
                "plan",
                f"--request={request_uri}",
                f"--execution-run-id={execution_id}",
            ])
        except Exception as error:
            failed = {
                **claim,
                "status": "failed",
                "error_class": type(error).__name__,
                "error_message": str(error)[:2000],
                "updated_at": datetime.now(UTC).isoformat(),
            }
            dispatch_blob.upload_from_string(
                json.dumps(failed, ensure_ascii=False, sort_keys=True).encode(),
                content_type="application/json",
            )
            raise
        accepted = {
            **claim,
            "status": "queued",
            "cloud_run_operation": operation,
            "updated_at": datetime.now(UTC).isoformat(),
        }
        dispatch_blob.upload_from_string(
            json.dumps(accepted, ensure_ascii=False, sort_keys=True).encode(),
            content_type="application/json",
        )
        return accepted

    def dispatch_action(self, execution_id: UUID, action: str) -> dict[str, Any]:
        if action not in {"resume-plan", "approve-axis"}:
            raise ValueError("unsupported product action")
        blob = self._client.bucket(self._bucket_name).blob(
            f"hefesto-product/executions/{execution_id}/{action}-dispatch.json"
        )
        claim = {
            "status": "dispatching",
            "execution_run_id": str(execution_id),
            "action": action,
            "created_at": datetime.now(UTC).isoformat(),
        }
        try:
            blob.upload_from_string(
                json.dumps(claim, sort_keys=True).encode(),
                content_type="application/json",
                if_generation_match=0,
            )
        except PreconditionFailed:
            existing = json.loads(blob.download_as_bytes())
            if existing.get("status") != "failed":
                return existing
        try:
            operation = self._dispatcher.run(
                [action, f"--execution-run-id={execution_id}"]
            )
        except Exception as error:
            failed = {
                **claim,
                "status": "failed",
                "error_class": type(error).__name__,
                "error_message": str(error)[:2000],
            }
            blob.upload_from_string(json.dumps(failed).encode(), content_type="application/json")
            raise
        accepted = {**claim, "status": "queued", "cloud_run_operation": operation}
        blob.upload_from_string(json.dumps(accepted).encode(), content_type="application/json")
        return accepted

    def dispatch_record(self, execution_id: UUID) -> dict[str, Any] | None:
        blob = self._client.bucket(self._bucket_name).blob(
            f"hefesto-product/executions/{execution_id}/plan-dispatch.json"
        )
        try:
            return json.loads(blob.download_as_bytes())
        except NotFound:
            return None


async def _product_receipt(execution_id: UUID) -> dict[str, Any] | None:
    repository = ProductRepository(_required("HEFESTO_POSTGRES_URL"))
    await repository.open()
    try:
        run = await repository.get_run(execution_id)
        if run is None:
            return None
        return {
            "status": run.state.value,
            "project_id": str(run.project_id),
            "execution_run_id": str(run.execution_run_id),
            "understanding": (
                run.understanding.model_dump(mode="json") if run.understanding else None
            ),
            "narrative_axis": run.axis.model_dump(mode="json") if run.axis else None,
            "editorial_project": (
                run.editorial_project.model_dump(mode="json")
                if run.editorial_project else None
            ),
            "error_class": run.error_class,
            "error_message": run.error_message,
            "usage": await repository.usage_report(execution_id),
        }
    finally:
        await repository.close()


ReceiptReader = Callable[[UUID], Any]


def create_app(
    *,
    client: storage.Client | None = None,
    dispatcher: Dispatcher | None = None,
    receipt_reader: ReceiptReader = _product_receipt,
    bucket_name: str | None = None,
) -> FastAPI:
    app = FastAPI(title="Hefesto Editorial API", version="1.2.0")

    def service() -> SubmissionService:
        return SubmissionService(
            client or storage.Client(project=_required("GOOGLE_CLOUD_PROJECT")),
            bucket_name or _required("HEFESTO_GCS_BUCKET"),
            dispatcher or _default_dispatcher(),
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ready", "service": "hefesto-editorial-api"}

    @app.post("/v1/plans", status_code=status.HTTP_202_ACCEPTED)
    async def submit_plan(request: EditorialInputRequest, response: Response) -> dict[str, Any]:
        try:
            result = await asyncio.to_thread(service().submit, request)
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(status_code=503, detail=f"dispatch failed: {error}") from error
        if result["status"] == "failed":
            raise HTTPException(status_code=503, detail=result)
        response.headers["Location"] = f"/v1/plans/{result['execution_run_id']}"
        return result

    @app.get("/v1/plans/{execution_id}")
    async def get_plan(execution_id: UUID) -> dict[str, Any]:
        receipt = await receipt_reader(execution_id)
        if receipt is not None:
            return receipt
        dispatch = await asyncio.to_thread(service().dispatch_record, execution_id)
        if dispatch is None:
            raise HTTPException(status_code=404, detail="unknown execution")
        return dispatch

    @app.post("/v1/plans/{execution_id}/resume", status_code=status.HTTP_202_ACCEPTED)
    async def resume_plan(execution_id: UUID) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, "resume-plan"
            )
        except Exception as error:
            raise HTTPException(status_code=503, detail=f"dispatch failed: {error}") from error

    @app.post(
        "/v1/plans/{execution_id}/approve-axis",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def approve_axis(execution_id: UUID) -> dict[str, Any]:
        try:
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, "approve-axis"
            )
        except Exception as error:
            raise HTTPException(status_code=503, detail=f"dispatch failed: {error}") from error

    return app


app = create_app()

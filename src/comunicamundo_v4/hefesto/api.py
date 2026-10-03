from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from hashlib import pbkdf2_hmac, sha256
import hmac
import inspect
import json
import os
from pathlib import Path
import re
import secrets
from typing import Any, Callable, Protocol
from uuid import UUID, uuid4, uuid5

import google.auth
from google.api_core.exceptions import NotFound, PreconditionFailed
from google.auth.transport.requests import AuthorizedSession
from google.cloud import storage
from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, model_validator

from .document_formats import (
    MAX_DOCUMENT_BYTES,
    resolve_document_format,
)
import requests

from .contracts import EditorialInputRequest
from .postgres import ProductRepository
from .storage import parse_gs_uri, persist_json_immutable


class Dispatcher(Protocol):
    def run(self, args: list[str]) -> str: ...


class AccessStore(Protocol):
    async def register_submission(self, execution_id: UUID, project_id: UUID, owner_id: str, payload: dict[str, Any]) -> None: ...
    async def assert_owner(self, execution_id: UUID, owner_id: str) -> None: ...
    async def list_submissions(self, owner_id: str) -> list[dict[str, Any]]: ...
    async def register_upload(self, **values: Any) -> None: ...
    async def get_upload(self, upload_id: UUID, owner_id: str) -> dict[str, Any] | None: ...
    async def complete_upload(self, upload_id: UUID, owner_id: str) -> None: ...
    async def owns_source(self, owner_id: str, object_uri: str) -> bool: ...
    async def claim_editorial_action(self, execution_id: UUID, token: str) -> bool: ...
    async def release_editorial_action(self, execution_id: UUID, token: str) -> None: ...


class AuthStore(Protocol):
    async def authenticate(self, username: str, password: str) -> bool: ...
    async def create_session(self, owner_id: str) -> str: ...
    async def session_owner(self, token: str) -> str | None: ...
    async def delete_session(self, token: str) -> None: ...


class PostgresAccessStore:
    async def _call(self, method: str, *args: Any, **kwargs: Any) -> Any:
        repository = ProductRepository(_required("HEFESTO_POSTGRES_URL"))
        await repository.open()
        try:
            return await getattr(repository, method)(*args, **kwargs)
        finally:
            await repository.close()

    async def register_submission(self, execution_id: UUID, project_id: UUID, owner_id: str, payload: dict[str, Any]) -> None:
        await self._call("register_submission", execution_id, project_id, owner_id, payload)

    async def assert_owner(self, execution_id: UUID, owner_id: str) -> None:
        stored = await self._call("submission_owner", execution_id)
        if stored is None:
            raise KeyError("unknown execution")
        if stored != owner_id:
            raise PermissionError("execution belongs to another user")

    async def list_submissions(self, owner_id: str) -> list[dict[str, Any]]:
        return await self._call("list_submissions", owner_id)

    async def register_upload(self, **values: Any) -> None:
        await self._call("register_upload", **values)

    async def get_upload(self, upload_id: UUID, owner_id: str) -> dict[str, Any] | None:
        return await self._call("get_upload", upload_id, owner_id)

    async def complete_upload(self, upload_id: UUID, owner_id: str) -> None:
        await self._call("complete_upload", upload_id, owner_id)

    async def owns_source(self, owner_id: str, object_uri: str) -> bool:
        return bool(await self._call("owns_source", owner_id, object_uri))

    async def claim_editorial_action(self, execution_id: UUID, token: str) -> bool:
        return bool(await self._call("claim_editorial_action", execution_id, token))

    async def release_editorial_action(self, execution_id: UUID, token: str) -> None:
        await self._call("release_editorial_action", execution_id, token)


class PostgresAuthStore:
    async def _call(self, method: str, *args: Any) -> Any:
        repository = ProductRepository(_required("HEFESTO_POSTGRES_URL"))
        await repository.open()
        try:
            return await getattr(repository, method)(*args)
        finally:
            await repository.close()

    async def authenticate(self, username: str, password: str) -> bool:
        credential = await self._call("auth_credential", username.strip().lower())
        if credential is None:
            await asyncio.to_thread(
                pbkdf2_hmac, "sha256", password.encode(), b"invalid-credential", 600_000
            )
            return False
        locked_until = credential["locked_until"]
        if locked_until is not None and locked_until > datetime.now(UTC):
            return False
        candidate = await asyncio.to_thread(
            pbkdf2_hmac,
            "sha256",
            password.encode(),
            credential["salt"],
            600_000,
        )
        valid = hmac.compare_digest(candidate, credential["password_hash"])
        await self._call("record_auth_result", credential["username"], valid)
        return valid

    async def create_session(self, owner_id: str) -> str:
        token = secrets.token_urlsafe(48)
        await self._call(
            "create_session",
            owner_id,
            sha256(token.encode()).hexdigest(),
            datetime.now(UTC) + timedelta(days=7),
        )
        return token

    async def session_owner(self, token: str) -> str | None:
        if not token:
            return None
        return await self._call("session_owner", sha256(token.encode()).hexdigest())

    async def delete_session(self, token: str) -> None:
        if token:
            await self._call("delete_session", sha256(token.encode()).hexdigest())


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
            json={"overrides": {
                "containerOverrides": [{"args": args}],
                "taskCount": 1,
                "timeout": "7200s",
            }},
            timeout=30,
        )
        response.raise_for_status()
        operation = response.json()
        if not operation.get("name"):
            raise RuntimeError("Cloud Run returned no operation identity")
        return str(operation["name"])


class UploadRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    file_name: str = Field(min_length=1, max_length=255)
    content_type: str
    size_bytes: int = Field(ge=1, le=MAX_DOCUMENT_BYTES)

    @model_validator(mode="after")
    def supported_document(self) -> "UploadRequest":
        resolve_document_format(self.file_name, self.content_type)
        return self


class LoginRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    username: str = Field(min_length=1, max_length=100)
    password: str = Field(min_length=1, max_length=200)


class ProvisionalRevisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    message: str = Field(min_length=1)


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
    payload = request.model_dump(mode="json", exclude_none=True)
    # Keep the byte identity of requests created before source modes existed.
    # The explicit zero-source mode remains present when the user selects it.
    if "source_mode" not in request.model_fields_set:
        payload.pop("source_mode", None)
    data = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode()
    return payload, data


def _execution_id(request: EditorialInputRequest, data: bytes) -> UUID:
    return uuid5(request.project_id, f"initial-plan:{sha256(data).hexdigest()}")


class SubmissionService:
    def __init__(
        self, client: storage.Client, bucket_name: str, dispatcher: Dispatcher
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
            args = [
                "plan",
                f"--request={request_uri}",
                f"--execution-run-id={execution_id}",
            ]
            operation = self._dispatcher.run(args)
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

    def dispatch_action(
        self,
        execution_id: UUID,
        action: str,
        *,
        extra_args: list[str] | None = None,
        dispatch_key: str | None = None,
    ) -> dict[str, Any]:
        if action not in {"revise-provisional", "revise-project", "accept-model-suggestion", "enrich-project-source", "source-search", "resume-plan", "approve-project", "produce-text"}:
            raise ValueError("unsupported product action")
        blob = self._client.bucket(self._bucket_name).blob(
            f"hefesto-product/executions/{execution_id}/{dispatch_key or action}-dispatch.json"
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
            created_at = datetime.fromisoformat(existing["created_at"])
            still_active = datetime.now(UTC) - created_at < timedelta(minutes=70)
            if existing.get("status") == "completed" or (
                existing.get("status") in {"dispatching", "queued"} and still_active
            ):
                return existing
        try:
            operation = self._dispatcher.run([
                action,
                f"--execution-run-id={execution_id}",
                f"--dispatch-key={dispatch_key or action}",
                *(extra_args or []),
            ])
        except Exception as error:
            failed = {
                **claim,
                "status": "failed",
                "error_class": type(error).__name__,
                "error_message": str(error)[:2000],
            }
            blob.upload_from_string(
                json.dumps(failed).encode(), content_type="application/json"
            )
            raise
        accepted = {
            **claim, "status": "queued", "cloud_run_operation": operation
        }
        blob.upload_from_string(
            json.dumps(accepted).encode(), content_type="application/json"
        )
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
            return await repository.product_receipt(execution_id)
        return await _canonical_receipt(repository, run)
    finally:
        await repository.close()


async def _canonical_receipt(
    repository: ProductRepository, run: Any
) -> dict[str, Any]:
    execution_id = run.execution_run_id
    # The canonical run owns editorial state; the submission receipt owns
    # per-file ingestion evidence, which must remain visible after prepare().
    stored = await repository.product_receipt(execution_id) or {}
    return {
        "status": run.state.value,
        "project_id": str(run.project_id),
        "execution_run_id": str(execution_id),
        "input": stored.get("input"),
        "editorial_project": (
            run.editorial_project.model_dump(mode="json")
            if run.editorial_project else None
        ),
        "final_text": (
            run.final_text.model_dump(mode="json") if run.final_text else None
        ),
        "processing": await repository.work_report(execution_id),
        "last_action_error": stored.get("last_action_error"),
        "error_class": run.error_class,
        "error_message": run.error_message,
        "stages": await repository.stage_report(execution_id),
        "usage": await repository.usage_report(execution_id),
    }


ReceiptReader = Callable[[UUID], Any]
IdentityReader = Callable[[Request], Any]


def create_app(
    *,
    client: storage.Client | None = None,
    dispatcher: Dispatcher | None = None,
    receipt_reader: ReceiptReader = _product_receipt,
    bucket_name: str | None = None,
    access_store: AccessStore | None = None,
    auth_store: AuthStore | None = None,
    identity_reader: IdentityReader | None = None,
) -> FastAPI:
    app = FastAPI(title="Hefesto Editorial API", version="2.0.0")
    access = access_store or PostgresAccessStore()
    authentication = auth_store or PostgresAuthStore()
    web_root = Path(__file__).resolve().parent.parent / "web"
    index_html = (web_root / "index.html").read_text(encoding="utf-8")
    app_asset = web_root / "assets" / "app.js"
    app_version = sha256(app_asset.read_bytes()).hexdigest()[:16]
    versioned_index_html = index_html.replace(
        'src="/assets/app.js"',
        f'src="/assets/app.js?v={app_version}"',
    )
    app.mount(
        "/assets", StaticFiles(directory=web_root / "assets"), name="assets"
    )

    def service() -> SubmissionService:
        return SubmissionService(
            client or storage.Client(project=_required("GOOGLE_CLOUD_PROJECT")),
            bucket_name or _required("HEFESTO_GCS_BUCKET"),
            dispatcher or _default_dispatcher(),
        )

    async def owner(request: Request) -> str:
        if identity_reader is not None:
            value = identity_reader(request)
            return await value if inspect.isawaitable(value) else value
        owner_id = await authentication.session_owner(
            request.cookies.get("hefesto_session", "")
        )
        if owner_id is None:
            raise HTTPException(status_code=401, detail="login required")
        return owner_id

    async def owned(execution_id: UUID, request: Request) -> str:
        owner_id = await owner(request)
        try:
            await access.assert_owner(execution_id, owner_id)
        except KeyError as error:
            raise HTTPException(status_code=404, detail="unknown execution") from error
        except PermissionError as error:
            raise HTTPException(
                status_code=403, detail="execution belongs to another user"
            ) from error
        return owner_id

    @app.get("/", include_in_schema=False)
    async def product() -> HTMLResponse:
        return HTMLResponse(
            versioned_index_html,
            headers={"Cache-Control": "no-store"},
        )

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ready", "service": "hefesto-editorial-api"}

    @app.post("/v1/login")
    async def login(credentials: LoginRequest, response: Response) -> dict[str, str]:
        if not await authentication.authenticate(
            credentials.username, credentials.password
        ):
            raise HTTPException(status_code=401, detail="usuário ou senha incorretos")
        token = await authentication.create_session("hefesto-team")
        response.set_cookie(
            "hefesto_session", token, max_age=7 * 24 * 60 * 60,
            secure=True, httponly=True, samesite="strict", path="/",
        )
        return {"username": credentials.username.strip().lower()}

    @app.post("/v1/logout", status_code=204)
    async def logout(request: Request, response: Response) -> Response:
        await authentication.delete_session(
            request.cookies.get("hefesto_session", "")
        )
        response.delete_cookie(
            "hefesto_session", path="/", secure=True, httponly=True,
            samesite="strict",
        )
        response.status_code = status.HTTP_204_NO_CONTENT
        return response

    @app.get("/v1/me")
    async def me(request: Request) -> dict[str, str]:
        return {"username": await owner(request)}

    @app.get("/v1/plans")
    async def plans(request: Request) -> dict[str, Any]:
        return {"plans": await access.list_submissions(await owner(request))}

    @app.post("/v1/uploads", status_code=status.HTTP_201_CREATED)
    async def begin_upload(
        upload: UploadRequest, request: Request
    ) -> dict[str, Any]:
        owner_id = await owner(request)
        safe_name = re.sub(
            r"[^A-Za-z0-9._-]+", "-", Path(upload.file_name).name
        ).strip(".-") or "source.bin"
        upload_id = uuid4()
        owner_key = sha256(owner_id.encode()).hexdigest()[:24]
        object_name = (
            f"hefesto-product/users/{owner_key}/uploads/{upload_id}/{safe_name}"
        )
        bucket_value = bucket_name or _required("HEFESTO_GCS_BUCKET")
        storage_client = client or storage.Client(
            project=_required("GOOGLE_CLOUD_PROJECT")
        )
        blob = storage_client.bucket(bucket_value).blob(object_name)
        try:
            session_url = await asyncio.to_thread(
                blob.create_resumable_upload_session,
                content_type=upload.content_type,
                size=upload.size_bytes,
                timeout=30,
            )
            await access.register_upload(
                upload_id=upload_id,
                owner_id=owner_id,
                object_uri=f"gs://{bucket_value}/{object_name}",
                file_name=upload.file_name,
                content_type=upload.content_type,
                size_bytes=upload.size_bytes,
                upload_session_url=session_url,
            )
        except Exception as error:
            raise HTTPException(
                status_code=503,
                detail=f"upload initialization failed: {error}",
            ) from error
        return {"upload_id": str(upload_id), "chunk_size": 8 * 1024 * 1024}

    @app.put("/v1/uploads/{upload_id}", status_code=204)
    async def upload_chunk(upload_id: UUID, request: Request) -> Response:
        record = await access.get_upload(upload_id, await owner(request))
        if record is None:
            raise HTTPException(status_code=404, detail="unknown upload")
        if (
            record["status"] != "uploading"
            or not record.get("upload_session_url")
        ):
            raise HTTPException(
                status_code=409, detail="upload is not accepting data"
            )
        content_range = request.headers.get("content-range", "")
        match = re.fullmatch(r"bytes (\d+)-(\d+)/(\d+)", content_range)
        body = await request.body()
        if not match or len(body) > 8 * 1024 * 1024:
            raise HTTPException(status_code=400, detail="invalid upload chunk")
        start, end, total = map(int, match.groups())
        if (
            end - start + 1 != len(body)
            or total != record["size_bytes"]
        ):
            raise HTTPException(
                status_code=400,
                detail="upload chunk range does not match content",
            )
        try:
            upstream = await asyncio.to_thread(
                requests.put,
                record["upload_session_url"],
                data=body,
                headers={
                    "Content-Length": str(len(body)),
                    "Content-Range": content_range,
                },
                timeout=60,
            )
        except requests.RequestException as error:
            raise HTTPException(
                status_code=503,
                detail=f"upload transport failed: {error}",
            ) from error
        if upstream.status_code not in {200, 201, 308}:
            raise HTTPException(
                status_code=502,
                detail=f"storage rejected upload chunk ({upstream.status_code})",
            )
        return Response(status_code=204)

    @app.post("/v1/uploads/{upload_id}/complete")
    async def complete_upload(
        upload_id: UUID, request: Request
    ) -> dict[str, Any]:
        owner_id = await owner(request)
        record = await access.get_upload(upload_id, owner_id)
        if record is None:
            raise HTTPException(status_code=404, detail="unknown upload")
        bucket, name = parse_gs_uri(record["object_uri"])
        blob = (
            client or storage.Client(project=_required("GOOGLE_CLOUD_PROJECT"))
        ).bucket(bucket).blob(name)
        try:
            await asyncio.to_thread(blob.reload)
        except NotFound as error:
            raise HTTPException(
                status_code=409, detail="upload has not completed"
            ) from error
        if int(blob.size or 0) != record["size_bytes"]:
            raise HTTPException(
                status_code=409,
                detail="stored file size does not match upload",
            )
        await access.complete_upload(upload_id, owner_id)
        return {
            "upload_id": str(upload_id),
            "status": "stored",
            "uri": record["object_uri"],
            "title": record["file_name"],
            "content_type": record["content_type"],
        }

    @app.post("/v1/plans", status_code=status.HTTP_202_ACCEPTED)
    async def submit_plan(
        editorial_request: EditorialInputRequest,
        request: Request,
        response: Response,
    ) -> dict[str, Any]:
        owner_id = await owner(request)
        for source in editorial_request.sources:
            if source.uri.startswith("gs://") and not await access.owns_source(
                owner_id, source.uri
            ):
                raise HTTPException(
                    status_code=403,
                    detail="a stored source does not belong to this user",
                )
        payload, data = _canonical_request(editorial_request)
        execution_id = _execution_id(editorial_request, data)
        await access.register_submission(
            execution_id, editorial_request.project_id, owner_id, payload
        )
        try:
            result = await asyncio.to_thread(
                service().submit, editorial_request
            )
        except ValueError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"dispatch failed: {error}"
            ) from error
        if result["status"] == "failed":
            raise HTTPException(status_code=503, detail=result)
        response.headers["Location"] = (
            f"/v1/plans/{result['execution_run_id']}"
        )
        return result

    @app.get("/v1/plans/{execution_id}")
    async def get_plan(
        execution_id: UUID, request: Request
    ) -> dict[str, Any]:
        await owned(execution_id, request)
        receipt = await receipt_reader(execution_id)
        if receipt is not None:
            return receipt
        dispatch = await asyncio.to_thread(
            service().dispatch_record, execution_id
        )
        if dispatch is None:
            raise HTTPException(status_code=404, detail="unknown execution")
        return dispatch

    async def required_receipt(
        execution_id: UUID, request: Request
    ) -> dict[str, Any]:
        await owned(execution_id, request)
        receipt = await receipt_reader(execution_id)
        if receipt is None:
            raise HTTPException(
                status_code=404, detail="unknown product execution"
            )
        return receipt

    @app.post(
        "/v1/plans/{execution_id}/revise-provisional-project",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def revise_provisional_project(
        execution_id: UUID,
        revision: ProvisionalRevisionRequest,
        request: Request,
    ) -> dict[str, Any]:
        receipt = await required_receipt(execution_id, request)
        if receipt["status"] != "provisional_project_proposed":
            raise HTTPException(
                status_code=409,
                detail=f"execution in {receipt['status']} is not awaiting provisional revision",
            )
        digest = sha256(revision.message.encode()).hexdigest()
        revision_uri = persist_json_immutable(
            client or storage.Client(project=_required("GOOGLE_CLOUD_PROJECT")),
            bucket_name=bucket_name or _required("HEFESTO_GCS_BUCKET"),
            name=f"hefesto-product/executions/{execution_id}/provisional-revisions/{digest}.json",
            payload={"message": revision.message},
        )
        try:
            return await asyncio.to_thread(
                service().dispatch_action,
                execution_id,
                "revise-provisional",
                extra_args=[f"--instruction={revision_uri}"],
                dispatch_key=f"revise-provisional-{digest[:16]}",
            )
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"dispatch failed: {error}"
            ) from error

    @app.post(
        "/v1/plans/{execution_id}/approve-provisional-project",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def approve_provisional_project(
        execution_id: UUID, request: Request
    ) -> dict[str, Any]:
        receipt = await required_receipt(execution_id, request)
        if receipt["status"] != "provisional_project_proposed":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"execution in {receipt['status']} has no provisional "
                    "Editorial Project awaiting approval"
                ),
            )
        try:
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, "source-search"
            )
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"dispatch failed: {error}"
            ) from error

    @app.post(
        "/v1/plans/{execution_id}/resume",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def resume_plan(
        execution_id: UUID, request: Request
    ) -> dict[str, Any]:
        receipt = await required_receipt(execution_id, request)
        enrichment = next((stage for stage in reversed(receipt.get("stages") or [])
                           if stage.get("stage") == "source_enrichment"), {})
        resume_enrichment = (receipt["status"] == "project_proposed"
                             and enrichment.get("status") == "paused"
                             and enrichment.get("resumable") is True)
        if not resume_enrichment and receipt["status"] not in {
            "failed", "searching_sources", "selecting", "comprehending",
            "source_structure_ready", "producing_text",
        }:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"execution in {receipt['status']} "
                    "does not require processing resume"
                ),
            )
        project = receipt.get("editorial_project") or {}
        if resume_enrichment:
            action = "enrich-project-source"
        elif receipt.get("provisional_editorial_project") and not project:
            action = "source-search"
        else:
            action = "produce-text" if project.get("approved") else "resume-plan"
        try:
            dispatch_key = (
                f"resume-{action}-"
                f"{sha256(datetime.now(UTC).isoformat().encode()).hexdigest()[:16]}"
            )
            if resume_enrichment:
                if not await access.claim_editorial_action(execution_id, dispatch_key):
                    raise HTTPException(status_code=409, detail="project is already being updated")
                try:
                    return await asyncio.to_thread(service().dispatch_action, execution_id, action,
                        extra_args=[f"--index={enrichment['opportunity_index']}"], dispatch_key=dispatch_key)
                except Exception:
                    await access.release_editorial_action(execution_id, dispatch_key)
                    raise
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, action, dispatch_key=dispatch_key,
            )
        except HTTPException:
            raise
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"dispatch failed: {error}"
            ) from error

    @app.post(
        "/v1/plans/{execution_id}/revise-project",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def revise_editorial_project(
        execution_id: UUID,
        revision: ProvisionalRevisionRequest,
        request: Request,
    ) -> dict[str, Any]:
        receipt = await required_receipt(execution_id, request)
        if receipt["status"] not in {"project_proposed", "project_approved", "text_ready"}:
            raise HTTPException(status_code=409, detail="project is not awaiting revision")
        project = receipt.get("editorial_project") or {}
        version = str(project.get("editorial_project_id") or "")
        digest = sha256((version + revision.message).encode()).hexdigest()
        uri = persist_json_immutable(
            client or storage.Client(project=_required("GOOGLE_CLOUD_PROJECT")),
            bucket_name=bucket_name or _required("HEFESTO_GCS_BUCKET"),
            name=f"hefesto-product/executions/{execution_id}/project-revisions/{digest}.json",
            payload={"message": revision.message},
        )
        token = f"revise-project-{digest[:16]}-{uuid4().hex[:12]}"
        if not await access.claim_editorial_action(execution_id, token):
            raise HTTPException(status_code=409, detail="project is already being updated")
        try:
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, "revise-project",
                extra_args=[f"--instruction={uri}"],
                dispatch_key=token,
            )
        except Exception as error:
            await access.release_editorial_action(execution_id, token)
            raise HTTPException(status_code=503, detail=f"dispatch failed: {error}") from error

    @app.post(
        "/v1/plans/{execution_id}/model-suggestions/{index}/accept",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def accept_model_suggestion(
        execution_id: UUID, index: int, request: Request,
    ) -> dict[str, Any]:
        receipt = await required_receipt(execution_id, request)
        if receipt["status"] != "project_proposed":
            raise HTTPException(status_code=409, detail="project is not awaiting suggestions")
        project = receipt.get("editorial_project") or {}
        options = (project.get("proposal") or {}).get("model_knowledge_suggestions") or []
        if index < 0 or index >= len(options):
            raise HTTPException(status_code=404, detail="unknown model suggestion")
        version = str(project.get("editorial_project_id") or "")
        token = f"model-suggestion-{version}-{index}-{uuid4().hex[:12]}"
        if not await access.claim_editorial_action(execution_id, token):
            raise HTTPException(status_code=409, detail="project is already being updated")
        try:
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, "accept-model-suggestion",
                extra_args=[f"--index={index}"],
                dispatch_key=token,
            )
        except Exception as error:
            await access.release_editorial_action(execution_id, token)
            raise HTTPException(status_code=503, detail=f"dispatch failed: {error}") from error

    @app.post(
        "/v1/plans/{execution_id}/source-suggestions/{index}/accept",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def accept_source_suggestion(
        execution_id: UUID, index: int, request: Request,
    ) -> dict[str, Any]:
        receipt = await required_receipt(execution_id, request)
        if receipt["status"] != "project_proposed":
            raise HTTPException(status_code=409, detail="project is not awaiting suggestions")
        project = receipt.get("editorial_project") or {}
        options = (project.get("proposal") or {}).get("source_enrichment_opportunities") or []
        if index < 0 or index >= len(options):
            raise HTTPException(status_code=404, detail="unknown source opportunity")
        version = str(project.get("editorial_project_id") or "")
        token = f"source-suggestion-{version}-{index}-{uuid4().hex[:12]}"
        if not await access.claim_editorial_action(execution_id, token):
            raise HTTPException(status_code=409, detail="project is already being updated")
        try:
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, "enrich-project-source",
                extra_args=[f"--index={index}"],
                dispatch_key=token,
            )
        except Exception as error:
            await access.release_editorial_action(execution_id, token)
            raise HTTPException(status_code=503, detail=f"dispatch failed: {error}") from error

    @app.post(
        "/v1/plans/{execution_id}/approve-project",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def approve_project(
        execution_id: UUID, request: Request
    ) -> dict[str, Any]:
        receipt = await required_receipt(execution_id, request)
        if receipt["status"] == "project_approved":
            return receipt
        if receipt["status"] != "project_proposed":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"execution in {receipt['status']} "
                    "has no proposed Editorial Project to approve"
                ),
            )
        try:
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, "approve-project"
            )
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"dispatch failed: {error}"
            ) from error

    @app.post(
        "/v1/plans/{execution_id}/produce-text",
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def produce_text(
        execution_id: UUID, request: Request
    ) -> dict[str, Any]:
        receipt = await required_receipt(execution_id, request)
        if receipt.get("final_text") is not None:
            return receipt
        if receipt["status"] != "project_approved":
            raise HTTPException(
                status_code=409,
                detail=(
                    f"execution in {receipt['status']} "
                    "has no approved Editorial Project to produce"
                ),
            )
        try:
            return await asyncio.to_thread(
                service().dispatch_action, execution_id, "produce-text"
            )
        except Exception as error:
            raise HTTPException(
                status_code=503, detail=f"dispatch failed: {error}"
            ) from error

    return app


app = create_app()


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

from .postgres import ProductRepository
from .storage import parse_gs_uri


class Dispatcher(Protocol):
    def run(self, args: list[str]) -> str: ...


class AccessStore(Protocol):
    async def register_upload(self, **values: Any) -> None: ...
    async def get_upload(self, upload_id: UUID, owner_id: str) -> dict[str, Any] | None: ...
    async def complete_upload(self, upload_id: UUID, owner_id: str) -> None: ...
    async def owns_source(self, owner_id: str, object_uri: str) -> bool: ...


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


    async def register_upload(self, **values: Any) -> None:
        await self._call("register_upload", **values)

    async def get_upload(self, upload_id: UUID, owner_id: str) -> dict[str, Any] | None:
        return await self._call("get_upload", upload_id, owner_id)

    async def complete_upload(self, upload_id: UUID, owner_id: str) -> None:
        await self._call("complete_upload", upload_id, owner_id)

    async def owns_source(self, owner_id: str, object_uri: str) -> bool:
        return bool(await self._call("owns_source", owner_id, object_uri))


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


IdentityReader = Callable[[Request], Any]


def create_app(
    *,
    client: storage.Client | None = None,
    dispatcher: Dispatcher | None = None,
    bucket_name: str | None = None,
    access_store: AccessStore | None = None,
    auth_store: AuthStore | None = None,
    identity_reader: IdentityReader | None = None,
    v8_backend: Any = None,
) -> FastAPI:
    app = FastAPI(title="Hefesto Editorial API", version="2.0.0")
    access = access_store or PostgresAccessStore()
    authentication = auth_store or PostgresAuthStore()
    web_root = Path(__file__).resolve().parent.parent / "web"
    app.mount(
        "/assets", StaticFiles(directory=web_root / "assets"), name="assets"
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


    @app.get("/health", include_in_schema=False)
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


    from .v8.routes import install
    install(app, owner, access, lambda: dispatcher or _default_dispatcher(), v8_backend)
    return app


app = create_app()



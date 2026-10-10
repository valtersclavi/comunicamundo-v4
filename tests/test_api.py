from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound, PreconditionFailed

from comunicamundo_v8.hefesto.api import (
    GoogleCloudRunDispatcher,
    create_app,
)


class Blob:
    def __init__(self, values: dict[str, bytes], name: str) -> None:
        self.values = values
        self.name = name

    def upload_from_string(self, data, **kwargs) -> None:
        if kwargs.get("if_generation_match") == 0 and self.name in self.values:
            raise PreconditionFailed("exists")
        self.values[self.name] = data.encode() if isinstance(data, str) else data

    def download_as_bytes(self) -> bytes:
        if self.name not in self.values:
            raise NotFound("missing")
        return self.values[self.name]

    def create_resumable_upload_session(self, **kwargs) -> str:
        return f"https://storage.example/upload/{self.name}"

    def reload(self) -> None:
        if self.name not in self.values:
            raise NotFound("missing")
        self.size = len(self.values[self.name])


class Bucket:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def blob(self, name: str) -> Blob:
        return Blob(self.values, name)


class Storage:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}

    def bucket(self, name: str) -> Bucket:
        return Bucket(self.values)


class Dispatcher:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, args: list[str]) -> str:
        self.calls.append(args)
        return f"operations/{len(self.calls)}"


class Access:
    def __init__(self) -> None:
        self.uploads: dict[UUID, dict[str, object]] = {}


    async def register_upload(self, **values) -> None:
        self.uploads[values["upload_id"]] = {**values, "status": "uploading"}

    async def get_upload(self, upload_id, owner_id):
        item = self.uploads.get(upload_id)
        return item if item and item["owner_id"] == owner_id else None

    async def complete_upload(self, upload_id, owner_id) -> None:
        self.uploads[upload_id]["status"] = "stored"

    async def owns_source(self, owner_id, object_uri) -> bool:
        return any(item["owner_id"] == owner_id and item["status"] == "stored" and item["object_uri"] == object_uri for item in self.uploads.values())


class Auth:
    def __init__(self) -> None:
        self.tokens: dict[str, str] = {}

    async def authenticate(self, username: str, password: str) -> bool:
        return username == "hefesto" and password == "correct-password"

    async def create_session(self, owner_id: str) -> str:
        self.tokens["test-session"] = owner_id
        return "test-session"

    async def session_owner(self, token: str):
        return self.tokens.get(token)

    async def delete_session(self, token: str) -> None:
        self.tokens.pop(token, None)


class Response:
    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, str]:
        return {"name": "operations/cloud-run"}


def request() -> dict[str, object]:
    return {
        "project_id": str(uuid4()),
        "brief": {
            "theme": "Tema genérico",
            "approach": "Intenção editorial do usuário",
            "duration_minutes": 10,
            "output_language": "pt-BR",
            "audience": "Público definido",
            "product": "article",
        },
        "sources": [
            {
                "uri": "https://public.example/source.pdf",
                "title": "Fonte",
                "language": "pt-BR",
            }
        ],
    }


def client(storage: Storage, dispatcher: Dispatcher, receipt=None, access=None) -> TestClient:
    async def receipt_reader(execution_id: UUID):
        return receipt

    return TestClient(
        create_app(
            client=storage,
            bucket_name="product",
            dispatcher=dispatcher,
            access_store=access or Access(),
            identity_reader=lambda request: request.headers.get("x-test-user", "editor@example.com"),
        )
    )


def test_cloud_run_dispatch_uses_job_override_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Session:
        def __init__(self, credentials) -> None:
            captured["credentials"] = credentials

        def post(self, url: str, **kwargs) -> Response:
            captured.update({"url": url, **kwargs})
            return Response()

    monkeypatch.setattr(
        "comunicamundo_v8.hefesto.api.google.auth.default",
        lambda **kwargs: ("credentials", "project"),
    )
    monkeypatch.setattr(
        "comunicamundo_v8.hefesto.api.AuthorizedSession", Session
    )
    operation = GoogleCloudRunDispatcher(
        "project", "us-central1", "hefesto-job"
    ).run(["plan", "--request=gs://bucket/input.json"])
    assert operation == "operations/cloud-run"
    assert captured["url"] == (
        "https://run.googleapis.com/v2/projects/project/locations/"
        "us-central1/jobs/hefesto-job:run"
    )
    assert captured["json"] == {
        "overrides": {
            "containerOverrides": [
                {"args": ["plan", "--request=gs://bucket/input.json"]}
            ],
            "taskCount": 1,
            "timeout": "7200s",
        }
    }
    assert captured["timeout"] == 30


def test_api_health_does_not_initialize_cloud_clients() -> None:
    async def no_receipt(execution_id: UUID):
        return None

    api = TestClient(create_app())
    assert api.get("/health").json() == {
        "status": "ready", "service": "hefesto-editorial-api"
    }


def test_upload_initialization_is_owned_and_chunked() -> None:
    access = Access()
    api = client(Storage(), Dispatcher(), access=access)
    response = api.post(
        "/v1/uploads",
        json={"file_name": "fonte.pdf", "content_type": "application/pdf", "size_bytes": 12},
    )
    assert response.status_code == 201
    assert response.json()["chunk_size"] == 8 * 1024 * 1024
    upload_id = UUID(response.json()["upload_id"])
    assert access.uploads[upload_id]["owner_id"] == "editor@example.com"

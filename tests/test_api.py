from __future__ import annotations

import json
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound, PreconditionFailed

from comunicamundo_v4.hefesto.api import GoogleCloudRunDispatcher, create_app


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


def client(storage: Storage, dispatcher: Dispatcher, receipt=None) -> TestClient:
    async def receipt_reader(execution_id: UUID):
        return receipt

    return TestClient(
        create_app(
            client=storage,
            bucket_name="product",
            dispatcher=dispatcher,
            receipt_reader=receipt_reader,
        )
    )


def test_api_submits_generic_manifest_and_returns_stable_execution() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher)
    payload = request()

    first = api.post("/v1/plans", json=payload)
    second = api.post("/v1/plans", json=payload)

    assert first.status_code == 202
    assert second.status_code == 202
    assert first.json()["execution_run_id"] == second.json()["execution_run_id"]
    assert first.headers["location"].endswith(first.json()["execution_run_id"])
    assert len(dispatcher.calls) == 1
    assert dispatcher.calls[0][0] == "plan"
    assert dispatcher.calls[0][1].startswith("--request=gs://product/")
    assert dispatcher.calls[0][2] == (
        f"--execution-run-id={first.json()['execution_run_id']}"
    )
    stored = json.loads(next(
        value for key, value in storage.values.items()
        if key.endswith("input-request.json")
    ))
    assert stored == payload


def test_cloud_run_dispatch_uses_job_override_contract(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class Session:
        def __init__(self, credentials) -> None:
            captured["credentials"] = credentials

        def post(self, url: str, **kwargs) -> Response:
            captured.update({"url": url, **kwargs})
            return Response()

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.api.google.auth.default",
        lambda **kwargs: ("credentials", "project"),
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.api.AuthorizedSession", Session
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
            "timeout": "3600s",
        }
    }
    assert captured["timeout"] == 30


def test_api_exposes_dispatch_state_before_product_run_exists() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher)
    submitted = api.post("/v1/plans", json=request()).json()
    status = api.get(f"/v1/plans/{submitted['execution_run_id']}")
    assert status.status_code == 200
    assert status.json()["status"] == "queued"
    assert status.json()["cloud_run_operation"] == "operations/1"


def test_api_dispatches_human_gate_as_separate_action() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher, receipt={"status": "axis_proposed"})
    submitted = api.post("/v1/plans", json=request()).json()
    execution_id = submitted["execution_run_id"]
    first = api.post(f"/v1/plans/{execution_id}/approve-axis")
    second = api.post(f"/v1/plans/{execution_id}/approve-axis")
    assert first.status_code == 202
    assert second.status_code == 202
    assert len(dispatcher.calls) == 2
    assert dispatcher.calls[1] == [
        "approve-axis", f"--execution-run-id={execution_id}"
    ]


def test_api_rejects_human_gate_before_axis_exists() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher, receipt={"status": "understanding_ready"})
    execution_id = uuid4()
    response = api.post(f"/v1/plans/{execution_id}/approve-axis")
    assert response.status_code == 409
    assert dispatcher.calls == []


def test_api_rejects_invalid_manifest_before_dispatch() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher)
    payload = request()
    payload["brief"]["duration_minutes"] = 0
    response = api.post("/v1/plans", json=payload)
    assert response.status_code == 422
    assert dispatcher.calls == []


def test_api_health_does_not_initialize_cloud_clients() -> None:
    async def no_receipt(execution_id: UUID):
        return None

    api = TestClient(create_app(receipt_reader=no_receipt))
    assert api.get("/healthz").json() == {
        "status": "ready", "service": "hefesto-editorial-api"
    }

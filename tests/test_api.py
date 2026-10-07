from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from google.api_core.exceptions import NotFound, PreconditionFailed

from comunicamundo_v4.hefesto.api import (
    GoogleCloudRunDispatcher,
    _canonical_receipt,
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
        self.submissions: dict[UUID, dict[str, object]] = {}
        self.uploads: dict[UUID, dict[str, object]] = {}

    async def register_submission(self, execution_id, project_id, owner_id, payload) -> None:
        existing = self.submissions.get(execution_id)
        if existing and existing["owner_id"] != owner_id:
            raise PermissionError("execution belongs to another user")
        self.submissions[execution_id] = {
            "execution_run_id": str(execution_id), "project_id": str(project_id),
            "owner_id": owner_id, "request": payload,
            "created_at": "2026-09-08T00:00:00Z", "updated_at": "2026-09-08T00:00:00Z",
        }

    async def assert_owner(self, execution_id, owner_id) -> None:
        item = self.submissions.get(execution_id)
        if not item:
            raise KeyError("unknown execution")
        if item["owner_id"] != owner_id:
            raise PermissionError("execution belongs to another user")

    async def list_submissions(self, owner_id):
        return [{key: value for key, value in item.items() if key != "owner_id"} for item in self.submissions.values() if item["owner_id"] == owner_id]

    async def claim_editorial_action(self, execution_id, token):
        submission = self.submissions[execution_id]
        if submission.get('active_action'):
            return False
        submission['active_action'] = token
        return True

    async def release_editorial_action(self, execution_id, token):
        submission = self.submissions[execution_id]
        if submission.get('active_action') == token:
            submission.pop('active_action')

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
            receipt_reader=receipt_reader,
            access_store=access or Access(),
            identity_reader=lambda request: request.headers.get("x-test-user", "editor@example.com"),
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


def test_removed_zero_source_mode_is_rejected_without_dispatch() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher)
    payload = request()
    payload.update({"source_mode": "find_for_me", "sources": []})
    response = api.post("/v1/plans", json=payload)
    assert response.status_code == 422
    assert dispatcher.calls == []


def test_zero_sources_are_rejected_without_find_for_me_mode() -> None:
    api = client(Storage(), Dispatcher())
    payload = request()
    payload["sources"] = []
    assert api.post("/v1/plans", json=payload).status_code == 422


def test_provisional_approval_dispatches_internal_source_search() -> None:
    storage, dispatcher, access = Storage(), Dispatcher(), Access()
    receipt = {"status": "provisional_project_proposed"}
    api = client(storage, dispatcher, receipt=receipt, access=access)
    payload = request()
    # Existing receipt is supplied by the fixture; new zero-source submissions are disabled.
    execution_id = api.post("/v1/plans", json=payload).json()["execution_run_id"]

    response = api.post(f"/v1/plans/{execution_id}/approve-provisional-project")

    assert response.status_code == 202
    assert dispatcher.calls[-1] == [
        "source-search", f"--execution-run-id={execution_id}",
        "--dispatch-key=source-search",
    ]


def test_provisional_approval_does_not_redispatch_active_source_search() -> None:
    storage, dispatcher, access = Storage(), Dispatcher(), Access()
    receipt = {"status": "searching_sources"}
    api = client(storage, dispatcher, receipt=receipt, access=access)
    payload = request()
    # Existing receipt is supplied by the fixture; new zero-source submissions are disabled.
    execution_id = api.post("/v1/plans", json=payload).json()["execution_run_id"]

    response = api.post(f"/v1/plans/{execution_id}/approve-provisional-project")

    assert response.status_code == 409
    assert dispatcher.calls[-1][0] == "plan"


def test_provisional_chat_dispatches_revision_with_immutable_instruction() -> None:
    storage, dispatcher, access = Storage(), Dispatcher(), Access()
    receipt = {"status": "provisional_project_proposed"}
    api = client(storage, dispatcher, receipt=receipt, access=access)
    payload = request()
    # Existing receipt is supplied by the fixture; new zero-source submissions are disabled.
    execution_id = api.post("/v1/plans", json=payload).json()["execution_run_id"]

    response = api.post(
        f"/v1/plans/{execution_id}/revise-provisional-project",
        json={"message": "Aprofunde o segundo assunto"},
    )

    assert response.status_code == 202
    assert dispatcher.calls[-1][0:2] == [
        "revise-provisional", f"--execution-run-id={execution_id}",
    ]
    assert dispatcher.calls[-1][2].startswith("--dispatch-key=revise-provisional-")
    assert dispatcher.calls[-1][3].startswith("--instruction=gs://product/")
    instruction = json.loads(next(
        value for key, value in storage.values.items()
        if "/provisional-revisions/" in key
    ))
    assert instruction == {"message": "Aprofunde o segundo assunto"}


def test_provisional_chat_does_not_start_source_search() -> None:
    storage, dispatcher, access = Storage(), Dispatcher(), Access()
    receipt = {"status": "provisional_project_proposed"}
    api = client(storage, dispatcher, receipt=receipt, access=access)
    payload = request()
    # Existing receipt is supplied by the fixture; new zero-source submissions are disabled.
    execution_id = api.post("/v1/plans", json=payload).json()["execution_run_id"]

    response = api.post(
        f"/v1/plans/{execution_id}/revise-provisional-project",
        json={"message": "Inclua um assunto antes do primeiro"},
    )

    assert response.status_code == 202
    assert dispatcher.calls[-1][0] == "revise-provisional"
    assert all(call[0] != "source-search" for call in dispatcher.calls)


def test_provisional_chat_is_rejected_after_preproject_approval() -> None:
    storage, dispatcher, access = Storage(), Dispatcher(), Access()
    receipt = {"status": "searching_sources"}
    api = client(storage, dispatcher, receipt=receipt, access=access)
    payload = request()
    # Existing receipt is supplied by the fixture; new zero-source submissions are disabled.
    execution_id = api.post("/v1/plans", json=payload).json()["execution_run_id"]

    response = api.post(
        f"/v1/plans/{execution_id}/revise-provisional-project",
        json={"message": "Inclua um assunto antes do primeiro"},
    )

    assert response.status_code == 409
    assert all(call[0] != "revise-provisional" for call in dispatcher.calls)


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
            "timeout": "7200s",
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


def test_api_has_no_separate_axis_approval_route() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher, receipt={"status": "comprehending"})
    execution_id = api.post("/v1/plans", json=request()).json()["execution_run_id"]
    response = api.post(f"/v1/plans/{execution_id}/approve-axis")
    assert response.status_code == 404
    assert len(dispatcher.calls) == 1


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
    assert api.get("/health").json() == {
        "status": "ready", "service": "hefesto-editorial-api"
    }


def test_product_interface_is_the_root_entrypoint() -> None:
    api = client(Storage(), Dispatcher())
    response = api.get("/")
    assert response.status_code == 200
    assert "Transforme fontes" in response.text
    assert "Produto final" in response.text
    assert '/assets/app.js?v=' in response.text
    assert response.headers["cache-control"] == "no-store"


def test_canonical_receipt_exposes_the_persisted_final_text() -> None:
    final_text = {
        "proposal": {"title": "Título", "body": "Texto"},
        "word_count": 1,
    }

    class Record:
        def model_dump(self, *, mode):
            assert mode == "json"
            return final_text

    class Repository:
        async def product_receipt(self, execution_id):
            return {"last_action_error": {"error_class": "ModelCallError", "error_message": "malformed command"},
                    "input": {"submitted_sources": [{"title": "sample.pdf"}],
                              "sources": [{"title": "sample.pdf", "stored_sentences": 4}]}}

        async def work_report(self, execution_id):
            return {}

        async def stage_report(self, execution_id):
            return []

        async def usage_report(self, execution_id):
            return {"call_count": 1}

    execution_id = uuid4()
    run = SimpleNamespace(
        state=SimpleNamespace(value="text_ready"),
        project_id=uuid4(),
        execution_run_id=execution_id,
        editorial_project=None,
        final_text=Record(),
        error_class=None,
        error_message=None,
    )
    receipt = asyncio.run(_canonical_receipt(Repository(), run))
    assert receipt["status"] == "text_ready"
    assert receipt["last_action_error"]["error_message"] == "malformed command"
    assert receipt["final_text"] == final_text
    assert receipt["input"]["sources"][0]["stored_sentences"] == 4


def test_shared_login_protects_product_operations_with_server_session() -> None:
    auth = Auth()
    api = TestClient(
        create_app(
            client=Storage(), bucket_name="product", dispatcher=Dispatcher(),
            access_store=Access(), auth_store=auth,
        ),
        base_url="https://hefesto.example",
    )
    assert api.get("/").status_code == 200
    assert api.get("/v1/me").status_code == 401
    assert api.post(
        "/v1/login", json={"username": "hefesto", "password": "wrong"}
    ).status_code == 401
    logged_in = api.post(
        "/v1/login", json={"username": "hefesto", "password": "correct-password"}
    )
    assert logged_in.status_code == 200
    assert "HttpOnly" in logged_in.headers["set-cookie"]
    assert "Secure" in logged_in.headers["set-cookie"]
    assert api.get("/v1/me").json() == {"username": "hefesto-team"}
    assert api.post("/v1/logout").status_code == 204
    assert api.get("/v1/me").status_code == 401


def test_execution_access_is_scoped_to_authenticated_user() -> None:
    access = Access()
    api = client(Storage(), Dispatcher(), access=access)
    submitted = api.post("/v1/plans", json=request()).json()
    response = api.get(
        f"/v1/plans/{submitted['execution_run_id']}",
        headers={"x-test-user": "another@example.com"},
    )
    assert response.status_code == 403


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


def test_uploaded_pdf_enters_the_same_generic_plan_path(monkeypatch) -> None:
    storage, dispatcher, access = Storage(), Dispatcher(), Access()
    api = client(storage, dispatcher, access=access)
    started = api.post(
        "/v1/uploads",
        json={"file_name": "qualquer-tema.pdf", "content_type": "application/pdf", "size_bytes": 11},
    ).json()

    class Upstream:
        status_code = 200

    def put(url, *, data, headers, timeout):
        name = url.removeprefix("https://storage.example/upload/")
        storage.values[name] = data
        assert headers["Content-Range"] == "bytes 0-10/11"
        return Upstream()

    monkeypatch.setattr("comunicamundo_v4.hefesto.api.requests.put", put)
    chunk = api.put(
        f"/v1/uploads/{started['upload_id']}",
        content=b"%PDF source",
        headers={"Content-Range": "bytes 0-10/11"},
    )
    assert chunk.status_code == 204
    completed = api.post(f"/v1/uploads/{started['upload_id']}/complete")
    assert completed.status_code == 200

    payload = request()
    payload["sources"] = [{
        "uri": completed.json()["uri"], "title": completed.json()["title"], "language": "pt-BR"
    }]
    submitted = api.post("/v1/plans", json=payload)
    assert submitted.status_code == 202
    assert dispatcher.calls[0][0] == "plan"


def test_project_approval_is_a_separate_human_action() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher, receipt={"status": "project_proposed"})
    execution_id = api.post("/v1/plans", json=request()).json()["execution_run_id"]
    response = api.post(f"/v1/plans/{execution_id}/approve-project")
    assert response.status_code == 202
    assert dispatcher.calls[-1] == [
        "approve-project", f"--execution-run-id={execution_id}",
        "--dispatch-key=approve-project",
    ]


def test_approved_existing_project_can_dispatch_final_text() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(
        storage,
        dispatcher,
        receipt={
            "status": "project_approved",
            "editorial_project": {"approved": True},
            "final_text": None,
        },
    )
    execution_id = api.post("/v1/plans", json=request()).json()["execution_run_id"]
    response = api.post(f"/v1/plans/{execution_id}/produce-text")
    assert response.status_code == 202
    assert dispatcher.calls[-1] == [
        "produce-text", f"--execution-run-id={execution_id}",
        "--dispatch-key=produce-text",
    ]


def test_failed_text_generation_resumes_at_production() -> None:
    storage, dispatcher = Storage(), Dispatcher()
    api = client(
        storage,
        dispatcher,
        receipt={
            "status": "failed",
            "editorial_project": {"approved": True},
            "final_text": None,
        },
    )
    execution_id = api.post("/v1/plans", json=request()).json()["execution_run_id"]
    response = api.post(f"/v1/plans/{execution_id}/resume")
    assert response.status_code == 202
    assert dispatcher.calls[-1][:2] == [
        "produce-text", f"--execution-run-id={execution_id}",
    ]
    assert dispatcher.calls[-1][2].startswith("--dispatch-key=resume-produce-text-")


def test_failed_zero_source_generation_resumes_at_source_search_with_fresh_dispatch() -> None:
    storage, dispatcher, access = Storage(), Dispatcher(), Access()
    api = client(
        storage,
        dispatcher,
        receipt={
            "status": "failed",
            "provisional_editorial_project": {"working_title": "Pre"},
            "editorial_project": None,
        },
        access=access,
    )
    payload = request()
    # Existing receipt is supplied by the fixture; new zero-source submissions are disabled.
    execution_id = api.post("/v1/plans", json=payload).json()["execution_run_id"]

    response = api.post(f"/v1/plans/{execution_id}/resume")

    assert response.status_code == 202
    assert dispatcher.calls[-1][:2] == [
        "source-search", f"--execution-run-id={execution_id}",
    ]
    assert dispatcher.calls[-1][2].startswith("--dispatch-key=resume-source-search-")


def test_stuck_source_search_can_be_resumed_with_fresh_dispatch() -> None:
    storage, dispatcher, access = Storage(), Dispatcher(), Access()
    api = client(
        storage,
        dispatcher,
        receipt={
            "status": "searching_sources",
            "provisional_editorial_project": {"working_title": "Pre"},
            "editorial_project": None,
        },
        access=access,
    )
    payload = request()
    # Existing receipt is supplied by the fixture; new zero-source submissions are disabled.
    execution_id = api.post("/v1/plans", json=payload).json()["execution_run_id"]

    response = api.post(f"/v1/plans/{execution_id}/resume")

    assert response.status_code == 202
    assert dispatcher.calls[-1][:2] == [
        "source-search", f"--execution-run-id={execution_id}",
    ]
    assert dispatcher.calls[-1][2].startswith("--dispatch-key=resume-source-search-")



def test_paused_external_enrichment_resumes_its_selected_opportunity():
    storage, dispatcher = Storage(), Dispatcher()
    api = client(storage, dispatcher, receipt={
        "status": "project_proposed", "editorial_project": {"approved": False},
        "stages": [{"stage": "source_enrichment", "status": "paused", "resumable": True,
                    "opportunity_index": 2}],
    })
    execution_id = api.post("/v1/plans", json=request()).json()["execution_run_id"]
    response = api.post(f"/v1/plans/{execution_id}/resume")
    assert response.status_code == 202
    assert dispatcher.calls[-1][:2] == ["enrich-project-source", f"--execution-run-id={execution_id}"]
    assert "--index=2" in dispatcher.calls[-1]

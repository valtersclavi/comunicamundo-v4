import inspect
import json
from uuid import uuid4

import pytest
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from comunicamundo_v4.hefesto.probe import (
    BOUNDARY_CONTRACT,
    ENABLED_BOUNDARIES,
    PAID_BOUNDARIES,
    PLAN_BOUNDARIES,
    ProbeBoundary,
    parse_boundary,
    run_probe,
)
from comunicamundo_v4.hefesto.worker import main


def _valid_request(path) -> None:
    path.write_text(
        json.dumps(
            {
                "project_id": str(uuid4()),
                "brief": {
                    "theme": "O vivenciar do luto na infância",
                    "approach": "Texto sensível para familiares",
                    "duration_minutes": 8,
                },
                "sources": [
                    {
                        "source_id": str(uuid4()),
                        "text": "Conteúdo confidencial da fonte",
                        "language": "pt",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )


def test_plan_startup_map_is_serial_and_complete() -> None:
    assert [item.value for item in PLAN_BOUNDARIES] == [
        "request-read",
        "persistence",
        "embed-construct",
        "graph-llm-construct",
        "axis-llm-construct",
        "graph-init",
        "graph-extract",
        "axis-generate",
    ]
    assert ENABLED_BOUNDARIES == {ProbeBoundary.REQUEST_READ}
    assert PAID_BOUNDARIES == {
        ProbeBoundary.GRAPH_EXTRACT,
        ProbeBoundary.AXIS_GENERATE,
    }
    assert set(BOUNDARY_CONTRACT) == set(PLAN_BOUNDARIES)


def test_request_read_passes_without_vertex_or_persistence(
    tmp_path, monkeypatch, capsys
) -> None:
    request_path = tmp_path / "request.json"
    _valid_request(request_path)
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "comunicamundo-v4")

    def fail_runtime(*args, **kwargs):
        raise AssertionError("Vertex runtime must not be built for request-read")

    def fail_persistence(*args, **kwargs):
        raise AssertionError("persistence must not be built for request-read")

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.build_vertex_runtime", fail_runtime
    )
    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.GcpEditorialPersistence.from_settings",
        fail_persistence,
    )
    main(["probe", "--boundary", "request-read", "--request", str(request_path)])
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["boundary"] == "request-read"
    assert payload["status"] == "pass"
    assert payload["paid"] is False
    assert payload["source_count"] == 1
    assert payload["duration_minutes"] == 8
    assert "Conteúdo confidencial" not in json.dumps(payload)


def test_request_read_fails_closed_on_extra_fields_before_runtime(
    tmp_path, monkeypatch, capsys
) -> None:
    request_path = tmp_path / "request.json"
    request_path.write_text(
        json.dumps(
            {
                "project_id": str(uuid4()),
                "brief": {"theme": "Tema", "approach": "Abordagem", "duration_minutes": 8},
                "sources": [
                    {
                        "source_id": str(uuid4()),
                        "text": "texto confidencial da fonte",
                        "language": "pt",
                    }
                ],
                "recipe": "blog-post-generator",
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "comunicamundo-v4")

    def fail_runtime(*args, **kwargs):
        raise AssertionError("Vertex runtime must not be built for an invalid request")

    monkeypatch.setattr(
        "comunicamundo_v4.hefesto.worker.build_vertex_runtime", fail_runtime
    )
    with pytest.raises(SystemExit) as caught:
        main(["probe", "--boundary", "request-read", "--request", str(request_path)])
    assert caught.value.code == 1
    printed = json.loads(capsys.readouterr().out.strip())
    assert printed["error_type"] == "ValidationError"
    assert "recipe" in printed["error"]
    assert "texto confidencial" not in printed["error"]


def test_request_read_requires_request_path(monkeypatch, capsys) -> None:
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "comunicamundo-v4")
    with pytest.raises(SystemExit) as caught:
        main(["probe", "--boundary", "request-read"])
    assert caught.value.code == 1
    printed = json.loads(capsys.readouterr().out.strip())
    assert printed["error_type"] == "ValueError"
    assert "request-read requires --request" in printed["error"]


def test_later_unpaid_boundaries_are_refused_until_request_read() -> None:
    for boundary in (
        ProbeBoundary.PERSISTENCE,
        ProbeBoundary.EMBED_CONSTRUCT,
        ProbeBoundary.GRAPH_LLM_CONSTRUCT,
        ProbeBoundary.AXIS_LLM_CONSTRUCT,
        ProbeBoundary.GRAPH_INIT,
    ):
        with pytest.raises(ValueError, match="refused until request-read is proven"):
            run_probe(boundary.value)


def test_paid_boundaries_are_refused() -> None:
    for boundary in PAID_BOUNDARIES:
        with pytest.raises(ValueError, match="paid model boundary"):
            run_probe(boundary.value)


def test_unknown_boundary_fails_closed() -> None:
    with pytest.raises(ValueError, match="unknown probe boundary"):
        parse_boundary("produce")


def test_embedding_constructor_does_not_fetch_model_metadata() -> None:
    source = inspect.getsource(GoogleGenAIEmbedding.__init__)
    assert "models.get" not in source
    assert "Client(**config_params)" in source


def test_graph_llm_still_fetches_metadata_unless_both_limits_are_set() -> None:
    source = inspect.getsource(GoogleGenAI.__init__)
    assert "if max_tokens is None or context_window is None" in source
    assert "client.models.get(model=model)" in source

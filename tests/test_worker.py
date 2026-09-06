from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.worker import _parser, main


def _commands(parser):
    return next(action.choices for action in parser._actions if getattr(action, "choices", None))


def test_parser_exposes_only_postgresql_pipeline_commands() -> None:
    assert set(_commands(_parser())) == {
        "migrate-postgres", "check-postgres", "run-v2", "run-proof-v2",
        "digest-v2", "resume-digest-v2", "propose-axis-v2", "approve-axis-v2",
        "approve-project-v2", "produce-v2", "report-v2",
    }


def test_v2_capacity_and_approval_flags_are_explicit() -> None:
    parser = _parser()
    run_id = str(uuid4())
    with pytest.raises(SystemExit):
        parser.parse_args(["digest-v2", "--request", "gs://bucket/request.json"])
    with pytest.raises(SystemExit):
        parser.parse_args([
            "approve-project-v2", "--execution-run-id", run_id,
            "--tokens-per-minute", "1000000",
        ])
    parsed = parser.parse_args([
        "approve-project-v2", "--execution-run-id", run_id,
        "--tokens-per-minute", "1000000", "--without-tips",
    ])
    assert parsed.command == "approve-project-v2"


def test_run_v2_requires_explicit_direct_approval() -> None:
    with pytest.raises(SystemExit):
        _parser().parse_args([
            "run-v2", "--request", "gs://bucket/request.json",
            "--tokens-per-minute", "1000000",
        ])


def test_report_v2_routes_to_postgresql_usage_report(monkeypatch, capsys) -> None:
    project_id = uuid4()

    async def report(value):
        assert value == project_id
        return SimpleNamespace(model_dump_json=lambda: '{"status":"ok"}')

    monkeypatch.setattr("comunicamundo_v4.hefesto.worker.run_report_v2_command", report)
    main(["report-v2", "--project-id", str(project_id)])
    assert capsys.readouterr().out.strip() == '{"status":"ok"}'

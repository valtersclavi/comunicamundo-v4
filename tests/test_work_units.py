from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.execution_contracts import (
    WorkUnit,
    WorkUnitStatus,
    text_sha256,
    work_unit_key,
)


INPUT_HASH = text_sha256("input")
MODEL_CONFIG_HASH = text_sha256("model-config")


def _unit(*, max_attempts: int = 3) -> WorkUnit:
    run_id = uuid4()
    key = work_unit_key(
        engine_version="v2",
        unit_type="source_dossier",
        input_sha256s=[INPUT_HASH],
        prompt_version="source-dossier.v1",
        model="gemini-2.5-flash",
        model_config_sha256=MODEL_CONFIG_HASH,
        cache_scope=f"run:{run_id}",
    )
    return WorkUnit(
        work_unit_id=key,
        execution_run_id=run_id,
        project_id=uuid4(),
        engine_version="v2",
        unit_type="source_dossier",
        input_uris=["gs://bucket/source.json"],
        input_sha256s=[INPUT_HASH],
        prompt_version="source-dossier.v1",
        model="gemini-2.5-flash",
        model_config_sha256=MODEL_CONFIG_HASH,
        cache_scope=f"run:{run_id}",
        reserved_capacity_tokens=20_000,
        max_attempts=max_attempts,
    )


def test_work_unit_key_is_deterministic_and_cache_scope_is_explicit() -> None:
    kwargs = dict(
        engine_version="v2",
        unit_type="source_dossier",
        input_sha256s=[INPUT_HASH],
        prompt_version="source-dossier.v1",
        model="gemini-2.5-flash",
        model_config_sha256=MODEL_CONFIG_HASH,
        cache_scope="run:one",
    )
    first = work_unit_key(**kwargs)

    assert work_unit_key(**kwargs) == first
    assert work_unit_key(**{**kwargs, "cache_scope": "run:two"}) != first


def test_work_unit_runs_through_durable_state_machine() -> None:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    pending = _unit()
    leased = pending.lease(owner="worker-1", now=now, ttl=timedelta(minutes=5))
    running = leased.start(owner="worker-1", now=now + timedelta(seconds=1))
    succeeded = running.succeed(
        owner="worker-1",
        now=now + timedelta(seconds=2),
        output_uri="gs://bucket/result.json",
        output_sha256=text_sha256("result"),
    )

    assert pending.status is WorkUnitStatus.PENDING
    assert leased.status is WorkUnitStatus.LEASED
    assert leased.attempt_count == 1
    assert running.status is WorkUnitStatus.RUNNING
    assert succeeded.status is WorkUnitStatus.SUCCEEDED
    assert succeeded.lease_owner is None
    assert succeeded.output_uri == "gs://bucket/result.json"
    with pytest.raises(ValueError, match="terminal"):
        succeeded.lease(owner="worker-2", now=now, ttl=timedelta(minutes=1))


def test_expired_lease_can_be_recovered_by_another_worker() -> None:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    leased = _unit().lease(owner="dead-worker", now=now, ttl=timedelta(seconds=5))

    with pytest.raises(ValueError, match="active lease"):
        leased.lease(owner="worker-2", now=now + timedelta(seconds=4), ttl=timedelta(minutes=1))

    recovered = leased.lease(owner="worker-2", now=now + timedelta(seconds=6), ttl=timedelta(minutes=1))
    assert recovered.lease_owner == "worker-2"
    assert recovered.attempt_count == 2


def test_retryable_failure_becomes_terminal_after_attempt_budget() -> None:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    first = _unit(max_attempts=2).lease(owner="w", now=now, ttl=timedelta(minutes=1))
    first = first.start(owner="w", now=now)
    retryable = first.fail(owner="w", now=now, error=RuntimeError("temporary"), retryable=True)
    second = retryable.lease(owner="w", now=now, ttl=timedelta(minutes=1)).start(owner="w", now=now)
    failed = second.fail(owner="w", now=now, error=RuntimeError("still broken"), retryable=True)

    assert retryable.status is WorkUnitStatus.RETRYABLE
    assert failed.status is WorkUnitStatus.FAILED
    assert failed.attempt_count == 2


def test_live_lease_rejects_wrong_worker_and_expiry() -> None:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    leased = _unit().lease(owner="worker-1", now=now, ttl=timedelta(seconds=5))

    with pytest.raises(ValueError, match="another worker"):
        leased.start(owner="worker-2", now=now)
    with pytest.raises(ValueError, match="expired"):
        leased.start(owner="worker-1", now=now + timedelta(seconds=5))


def test_state_transition_revalidates_persisted_artifact_contract() -> None:
    now = datetime(2026, 8, 30, tzinfo=UTC)
    running = _unit().lease(owner="w", now=now, ttl=timedelta(minutes=1)).start(owner="w", now=now)

    with pytest.raises(ValidationError):
        running.succeed(
            owner="w",
            now=now,
            output_uri="https://not-gcs.example/result.json",
            output_sha256="not-a-hash",
        )

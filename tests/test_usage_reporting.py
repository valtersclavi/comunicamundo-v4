from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID, uuid4

from comunicamundo_v4.hefesto.execution_contracts import (
    LLMAttemptRecord,
    LLMCallStatus,
    LLMUsage,
)
from comunicamundo_v4.hefesto.usage_reporting import aggregate_project_usage


PROJECT_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
SOURCE_URL = "https://cloud.google.com/vertex-ai/generative-ai/pricing"


def _attempt(
    *,
    task: str,
    status: LLMCallStatus = LLMCallStatus.SUCCEEDED,
    offset: int = 0,
    call_id: UUID | None = None,
    rated: bool = True,
    provider_reported: bool = True,
) -> LLMAttemptRecord:
    now = datetime(2026, 8, 31, 12, 0, tzinfo=UTC) + timedelta(seconds=offset)
    succeeded = status is LLMCallStatus.SUCCEEDED
    usage = (
        LLMUsage(
            provider_reported=provider_reported,
            prompt_tokens=100,
            cached_tokens=10,
            output_tokens=20,
            reasoning_tokens=5,
            total_tokens=125,
        )
        if succeeded
        else None
    )
    return LLMAttemptRecord(
        call_id=call_id or uuid4(),
        execution_run_id=uuid4(),
        project_id=PROJECT_ID,
        work_unit_id=f"{task}:{offset}",
        task_type=task,
        prompt_version="v1",
        model="gemini-2.5-flash",
        location="us-central1",
        attempt=1,
        status=status,
        queued_at=now,
        started_at=now + timedelta(milliseconds=100),
        finished_at=now + timedelta(milliseconds=600),
        queue_wait_ms=100,
        latency_ms=500,
        estimated_input_tokens=110,
        reserved_capacity_tokens=200,
        usage=usage,
        retryable=status is not LLMCallStatus.SUCCEEDED,
        error_class=None if succeeded else "TimeoutError",
        error_message=None if succeeded else "ambiguous timeout",
        rate_card_effective_at=now if succeeded and rated else None,
        rate_card_source_url=SOURCE_URL if succeeded and rated else None,
        rated_cost_usd=Decimal("0.001") if succeeded and rated else None,
    )


def test_usage_report_aggregates_real_provider_usage_by_stage_and_total() -> None:
    records = [_attempt(task="source_dossier"), _attempt(task="article_draft", offset=2)]
    report = aggregate_project_usage(
        PROJECT_ID,
        records,
        generated_at=datetime(2026, 8, 31, 13, 0, tzinfo=UTC),
    )

    assert report.call_count == 2
    assert report.attempt_count == 2
    assert report.prompt_tokens == 200
    assert report.reasoning_tokens == 10
    assert report.total_tokens == 250
    assert report.provider_latency_ms == 1_000
    assert report.wall_clock_ms == 2_600
    assert report.rated_cost_usd == Decimal("0.002")
    assert report.usage_complete is True
    assert report.pricing_complete is True
    assert report.embedding_calls == 0
    assert [stage.task_type for stage in report.stages] == ["article_draft", "source_dossier"]


def test_unknown_outcome_is_visible_and_prevents_false_cost_completeness() -> None:
    call_id = uuid4()
    records = [
        _attempt(task="article_draft", status=LLMCallStatus.UNKNOWN_OUTCOME, call_id=call_id),
        _attempt(task="article_draft", offset=2, call_id=call_id),
    ]
    report = aggregate_project_usage(PROJECT_ID, records)

    assert report.call_count == 1
    assert report.attempt_count == 2
    assert report.unknown_outcome_attempts == 1
    assert report.rated_cost_usd == Decimal("0.001")
    assert report.usage_complete is False
    assert report.pricing_complete is False
    assert report.billing_reconciled_cost_usd is None


def test_unrated_success_preserves_tokens_but_marks_pricing_incomplete() -> None:
    report = aggregate_project_usage(PROJECT_ID, [_attempt(task="axis", rated=False)])

    assert report.prompt_tokens == 100
    assert report.usage_complete is True
    assert report.pricing_complete is False
    assert report.rated_cost_usd == 0

"""Project-level token, latency, and cost reporting from immutable provider attempts."""
from __future__ import annotations

import asyncio
from collections import defaultdict
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from typing import Any, Protocol
from uuid import UUID

from google.api_core.exceptions import AlreadyExists, PreconditionFailed
from google.cloud.firestore_v1.base_query import FieldFilter
from pydantic import Field

from .contracts import StrictModel
from .digestion_contracts import ArtifactPointer
from .execution_contracts import LLMAttemptRecord, LLMCallStatus
from .execution_persistence import LLM_CALLS_COLLECTION


class UsageStageSummary(StrictModel):
    task_type: str
    model: str
    attempt_count: int = Field(ge=0)
    call_count: int = Field(ge=0)
    succeeded_attempts: int = Field(ge=0)
    failed_attempts: int = Field(ge=0)
    unknown_outcome_attempts: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    cached_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    tool_prompt_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    queue_wait_ms: int = Field(ge=0)
    provider_latency_ms: int = Field(ge=0)
    rated_cost_usd: Decimal = Field(ge=0)
    usage_complete: bool
    pricing_complete: bool


class ProjectUsageReport(StrictModel):
    report_version: str = "hefesto-v2-usage-report-v1"
    project_id: UUID
    generated_at: datetime
    first_queued_at: datetime | None
    last_finished_at: datetime | None
    wall_clock_ms: int = Field(ge=0)
    attempt_count: int = Field(ge=0)
    call_count: int = Field(ge=0)
    succeeded_attempts: int = Field(ge=0)
    failed_attempts: int = Field(ge=0)
    unknown_outcome_attempts: int = Field(ge=0)
    prompt_tokens: int = Field(ge=0)
    cached_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    reasoning_tokens: int = Field(ge=0)
    tool_prompt_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    embedding_calls: int = 0
    embedding_tokens: int = 0
    queue_wait_ms: int = Field(ge=0)
    provider_latency_ms: int = Field(ge=0)
    rated_cost_usd: Decimal = Field(ge=0)
    usage_complete: bool
    pricing_complete: bool
    rate_card_source_urls: list[str]
    billing_reconciled_cost_usd: Decimal | None = Field(default=None, ge=0)
    billing_reconciliation_status: str = "not_reconciled"
    stages: list[UsageStageSummary]


class UsageReportResult(StrictModel):
    report: ProjectUsageReport
    artifact: ArtifactPointer


def aggregate_project_usage(
    project_id: UUID,
    attempts: list[LLMAttemptRecord],
    *,
    generated_at: datetime | None = None,
) -> ProjectUsageReport:
    foreign = [record for record in attempts if record.project_id != project_id]
    if foreign:
        raise ValueError("usage report contains attempts from another project")
    ordered = sorted(attempts, key=lambda record: (record.queued_at, record.attempt, str(record.attempt_id)))
    groups: dict[tuple[str, str], list[LLMAttemptRecord]] = defaultdict(list)
    for record in ordered:
        groups[(record.task_type, record.model)].append(record)
    stages = [_summarize_stage(task, model, records) for (task, model), records in sorted(groups.items())]
    first = min((record.queued_at for record in ordered), default=None)
    last = max((record.finished_at for record in ordered), default=None)
    return ProjectUsageReport(
        project_id=project_id,
        generated_at=generated_at or datetime.now(UTC),
        first_queued_at=first,
        last_finished_at=last,
        wall_clock_ms=_duration_ms(first, last),
        attempt_count=len(ordered),
        call_count=len({record.call_id for record in ordered}),
        succeeded_attempts=sum(record.status is LLMCallStatus.SUCCEEDED for record in ordered),
        failed_attempts=sum(record.status is LLMCallStatus.FAILED for record in ordered),
        unknown_outcome_attempts=sum(record.status is LLMCallStatus.UNKNOWN_OUTCOME for record in ordered),
        **_usage_totals(ordered),
        queue_wait_ms=sum(record.queue_wait_ms for record in ordered),
        provider_latency_ms=sum(record.latency_ms for record in ordered),
        rated_cost_usd=sum((record.rated_cost_usd or Decimal("0") for record in ordered), Decimal("0")),
        usage_complete=_usage_complete(ordered),
        pricing_complete=_pricing_complete(ordered),
        rate_card_source_urls=sorted(
            {record.rate_card_source_url for record in ordered if record.rate_card_source_url}
        ),
        stages=stages,
    )


def _summarize_stage(
    task_type: str, model: str, records: list[LLMAttemptRecord]
) -> UsageStageSummary:
    return UsageStageSummary(
        task_type=task_type,
        model=model,
        attempt_count=len(records),
        call_count=len({record.call_id for record in records}),
        succeeded_attempts=sum(record.status is LLMCallStatus.SUCCEEDED for record in records),
        failed_attempts=sum(record.status is LLMCallStatus.FAILED for record in records),
        unknown_outcome_attempts=sum(record.status is LLMCallStatus.UNKNOWN_OUTCOME for record in records),
        **_usage_totals(records),
        queue_wait_ms=sum(record.queue_wait_ms for record in records),
        provider_latency_ms=sum(record.latency_ms for record in records),
        rated_cost_usd=sum((record.rated_cost_usd or Decimal("0") for record in records), Decimal("0")),
        usage_complete=_usage_complete(records),
        pricing_complete=_pricing_complete(records),
    )


def _usage_totals(records: list[LLMAttemptRecord]) -> dict[str, int]:
    names = (
        "prompt_tokens",
        "cached_tokens",
        "output_tokens",
        "reasoning_tokens",
        "tool_prompt_tokens",
        "total_tokens",
    )
    return {
        name: sum(getattr(record.usage, name) for record in records if record.usage is not None)
        for name in names
    }


def _usage_complete(records: list[LLMAttemptRecord]) -> bool:
    return bool(records) and all(
        record.status is not LLMCallStatus.UNKNOWN_OUTCOME
        and (record.status is not LLMCallStatus.SUCCEEDED or (record.usage and record.usage.provider_reported))
        for record in records
    )


def _pricing_complete(records: list[LLMAttemptRecord]) -> bool:
    successful = [record for record in records if record.status is LLMCallStatus.SUCCEEDED]
    return _usage_complete(records) and bool(successful) and all(
        record.rated_cost_usd is not None for record in successful
    )


def _duration_ms(start: datetime | None, finish: datetime | None) -> int:
    if start is None or finish is None:
        return 0
    return max(0, int((finish - start).total_seconds() * 1000))


class FirestoreUsageReader:
    def __init__(self, firestore: Any, *, collection: str = LLM_CALLS_COLLECTION) -> None:
        self._firestore = firestore
        self._collection = collection

    async def list_for_project(self, project_id: UUID) -> list[LLMAttemptRecord]:
        return await asyncio.to_thread(self._list_sync, project_id)

    def _list_sync(self, project_id: UUID) -> list[LLMAttemptRecord]:
        query = self._firestore.collection(self._collection).where(
            filter=FieldFilter("project_id", "==", str(project_id))
        )
        records = []
        for snapshot in query.stream():
            data = snapshot.to_dict()
            if data is not None:
                records.append(LLMAttemptRecord.model_validate(data))
        return records


class GcsUsageReportStore:
    def __init__(self, storage_client: Any, *, bucket_name: str, root_prefix: str) -> None:
        self._bucket = storage_client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._root = root_prefix.strip("/")
        if not self._root:
            raise ValueError("root prefix cannot be empty")

    async def persist(self, report: ProjectUsageReport) -> ArtifactPointer:
        return await asyncio.to_thread(self._persist_sync, report)

    def _persist_sync(self, report: ProjectUsageReport) -> ArtifactPointer:
        data = report.model_dump_json().encode("utf-8")
        digest = sha256(data).hexdigest()
        name = f"{self._root}/projects/{report.project_id}/usage/{digest}.json"
        blob = self._bucket.blob(name)
        try:
            blob.upload_from_string(data, content_type="application/json", if_generation_match=0)
        except (PreconditionFailed, AlreadyExists):
            if blob.download_as_bytes() != data:
                raise ValueError("conflicting immutable usage report")
        return ArtifactPointer(uri=f"gs://{self._bucket_name}/{name}", sha256=digest)


async def build_and_persist_usage_report(
    project_id: UUID,
    *,
    reader: FirestoreUsageReader,
    store: GcsUsageReportStore,
) -> UsageReportResult:
    report = aggregate_project_usage(project_id, await reader.list_for_project(project_id))
    return UsageReportResult(report=report, artifact=await store.persist(report))

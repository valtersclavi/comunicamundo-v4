import asyncio
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.execution_scheduler import (
    BoundedFairExecutor,
    FairProjectQueue,
    ScheduledWork,
    SmoothedTokenBucket,
)


def test_fair_project_queue_round_robins_instead_of_draining_large_project() -> None:
    project_a, project_b, project_c = uuid4(), uuid4(), uuid4()
    queue = FairProjectQueue[str]()
    for item in ("A1", "A2", "A3"):
        queue.put(project_a, item)
    for item in ("B1", "B2"):
        queue.put(project_b, item)
    queue.put(project_c, "C1")

    assert [queue.get()[1] for _ in range(6)] == ["A1", "B1", "C1", "A2", "B2", "A3"]


@pytest.mark.asyncio
async def test_token_bucket_smooths_bursts_across_time() -> None:
    now = [0.0]
    sleeps = []

    async def advance(seconds: float) -> None:
        sleeps.append(seconds)
        now[0] += seconds

    bucket = SmoothedTokenBucket(
        tokens_per_minute=600,
        burst_window_seconds=2,
        clock=lambda: now[0],
        sleep=advance,
    )

    assert bucket.burst_capacity == 20
    await bucket.reserve(15)
    await bucket.reserve(15)
    assert sleeps == [1.0]
    with pytest.raises(ValueError, match="must be split"):
        await bucket.reserve(21)


@pytest.mark.asyncio
async def test_executor_is_fair_when_only_one_call_can_run() -> None:
    projects = [uuid4(), uuid4(), uuid4()]
    started = []

    def operation(name):
        async def run():
            started.append(name)
            await asyncio.sleep(0)
            return name

        return run

    work = []
    for project, prefix in zip(projects, ("A", "B", "C"), strict=True):
        work.extend(
            ScheduledWork(
                work_unit_id=f"{prefix}{number}",
                project_id=project,
                reserved_capacity_tokens=1,
                operation=operation(f"{prefix}{number}"),
            )
            for number in (1, 2)
        )
    bucket = SmoothedTokenBucket(tokens_per_minute=120_000, burst_window_seconds=5)
    executor = BoundedFairExecutor(bucket, max_concurrency=1)

    outcomes = await executor.run_all(work)

    assert started == ["A1", "B1", "C1", "A2", "B2", "C2"]
    assert len(outcomes) == 6
    assert all(outcome.error is None for outcome in outcomes)


@pytest.mark.asyncio
async def test_executor_bounds_one_hundred_projects_and_contains_failure() -> None:
    active = 0
    peak = 0

    def operation(number):
        async def run():
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.001)
            active -= 1
            if number == 17:
                raise RuntimeError("isolated failure")
            return number

        return run

    work = [
        ScheduledWork(
            work_unit_id=str(number),
            project_id=uuid4(),
            reserved_capacity_tokens=1,
            operation=operation(number),
        )
        for number in range(100)
    ]
    bucket = SmoothedTokenBucket(tokens_per_minute=1_200_000, burst_window_seconds=5)
    executor = BoundedFairExecutor(bucket, max_concurrency=7, per_project_concurrency=1)

    outcomes = await executor.run_all(work)

    assert peak == 7
    assert len(outcomes) == 100
    assert sum(outcome.error is not None for outcome in outcomes) == 1
    assert next(outcome for outcome in outcomes if outcome.work_unit_id == "17").error is not None


@pytest.mark.asyncio
async def test_executor_rejects_oversized_unit_before_starting_any_work() -> None:
    started = []

    async def run():
        started.append(True)

    bucket = SmoothedTokenBucket(tokens_per_minute=600, burst_window_seconds=2)
    executor = BoundedFairExecutor(bucket, max_concurrency=2)
    work = [
        ScheduledWork(
            work_unit_id="small",
            project_id=uuid4(),
            reserved_capacity_tokens=1,
            operation=run,
        ),
        ScheduledWork(
            work_unit_id="oversized",
            project_id=uuid4(),
            reserved_capacity_tokens=21,
            operation=run,
        ),
    ]

    with pytest.raises(ValueError, match="must be split"):
        await executor.run_all(work)

    assert started == []

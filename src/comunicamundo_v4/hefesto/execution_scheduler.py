"""Small, deterministic admission layer for bounded Hefesto V2 concurrency."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from time import monotonic
from typing import Generic, TypeVar
from uuid import UUID


ItemT = TypeVar("ItemT")
ResultT = TypeVar("ResultT")


class FairProjectQueue(Generic[ItemT]):
    """Round-robin queue that prevents one large project from owning the head."""

    def __init__(self) -> None:
        self._items: dict[UUID, deque[ItemT]] = {}
        self._projects: deque[UUID] = deque()
        self._size = 0

    def put(self, project_id: UUID, item: ItemT) -> None:
        queue = self._items.get(project_id)
        if queue is None:
            queue = deque()
            self._items[project_id] = queue
            self._projects.append(project_id)
        queue.append(item)
        self._size += 1

    def get(self) -> tuple[UUID, ItemT]:
        if not self._projects:
            raise IndexError("fair queue is empty")
        project_id = self._projects.popleft()
        queue = self._items[project_id]
        item = queue.popleft()
        self._size -= 1
        if queue:
            self._projects.append(project_id)
        else:
            del self._items[project_id]
        return project_id, item

    def __len__(self) -> int:
        return self._size


class SmoothedTokenBucket:
    """Continuous TPM refill with a deliberately short burst window."""

    def __init__(
        self,
        *,
        tokens_per_minute: int,
        burst_window_seconds: float = 5.0,
        clock: Callable[[], float] = monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        if tokens_per_minute <= 0 or not 0 < burst_window_seconds <= 60:
            raise ValueError("token bucket requires positive TPM and a burst window of at most one minute")
        self._rate_per_second = tokens_per_minute / 60.0
        self._capacity = max(1.0, self._rate_per_second * burst_window_seconds)
        self._available = self._capacity
        self._clock = clock
        self._sleep = sleep
        self._updated_at = clock()
        self._lock = asyncio.Lock()

    @property
    def burst_capacity(self) -> int:
        return int(self._capacity)

    def validate(self, tokens: int) -> None:
        if tokens <= 0:
            raise ValueError("reservation must be positive")
        if tokens > self._capacity:
            raise ValueError("work unit exceeds the smoothing window and must be split")

    async def reserve(self, tokens: int) -> None:
        self.validate(tokens)
        while True:
            async with self._lock:
                self._refill()
                if self._available >= tokens:
                    self._available -= tokens
                    return
                wait_seconds = (tokens - self._available) / self._rate_per_second
            await self._sleep(wait_seconds)

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._updated_at)
        self._available = min(self._capacity, self._available + elapsed * self._rate_per_second)
        self._updated_at = now


@dataclass(frozen=True, slots=True)
class ScheduledWork(Generic[ResultT]):
    work_unit_id: str
    project_id: UUID
    reserved_capacity_tokens: int
    operation: Callable[[], Awaitable[ResultT]]


@dataclass(frozen=True, slots=True)
class ScheduledOutcome(Generic[ResultT]):
    work_unit_id: str
    project_id: UUID
    value: ResultT | None = None
    error: Exception | None = None


class BoundedFairExecutor:
    """Uses just enough concurrency to fill capacity while containing failures."""

    def __init__(
        self,
        bucket: SmoothedTokenBucket,
        *,
        max_concurrency: int,
        per_project_concurrency: int = 1,
    ) -> None:
        if max_concurrency <= 0 or per_project_concurrency <= 0:
            raise ValueError("concurrency limits must be positive")
        self._bucket = bucket
        self._max_concurrency = max_concurrency
        self._per_project_concurrency = per_project_concurrency

    async def run_all(self, work: Iterable[ScheduledWork[ResultT]]) -> list[ScheduledOutcome[ResultT]]:
        queue: FairProjectQueue[ScheduledWork[ResultT]] = FairProjectQueue()
        total = 0
        for item in work:
            self._bucket.validate(item.reserved_capacity_tokens)
            queue.put(item.project_id, item)
            total += 1
        outcomes: list[ScheduledOutcome[ResultT]] = []
        running: set[asyncio.Task[ScheduledOutcome[ResultT]]] = set()
        per_project: dict[UUID, int] = {}

        while len(outcomes) < total:
            deferred: list[tuple[UUID, ScheduledWork[ResultT]]] = []
            while len(queue) and len(running) < self._max_concurrency:
                project_id, item = queue.get()
                if per_project.get(project_id, 0) >= self._per_project_concurrency:
                    deferred.append((project_id, item))
                    if len(deferred) >= len(queue) + 1:
                        break
                    continue
                await self._bucket.reserve(item.reserved_capacity_tokens)
                per_project[project_id] = per_project.get(project_id, 0) + 1
                running.add(asyncio.create_task(self._run_one(item)))
            for project_id, item in deferred:
                queue.put(project_id, item)

            if not running:
                if len(queue):
                    continue
                break
            completed, running = await asyncio.wait(running, return_when=asyncio.FIRST_COMPLETED)
            for task in completed:
                outcome = task.result()
                outcomes.append(outcome)
                per_project[outcome.project_id] -= 1

        return outcomes

    @staticmethod
    async def _run_one(item: ScheduledWork[ResultT]) -> ScheduledOutcome[ResultT]:
        try:
            return ScheduledOutcome(
                work_unit_id=item.work_unit_id,
                project_id=item.project_id,
                value=await item.operation(),
            )
        except Exception as error:
            return ScheduledOutcome(
                work_unit_id=item.work_unit_id,
                project_id=item.project_id,
                error=error,
            )

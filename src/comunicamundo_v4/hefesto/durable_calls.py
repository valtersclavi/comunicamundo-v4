"""One durable execution procedure for every Hefesto V2 model call."""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, datetime, timedelta
from typing import Protocol, TypeVar, cast
from uuid import UUID

from pydantic import BaseModel

from .digestion_contracts import ArtifactPointer
from .execution_contracts import LLMCallStatus, WorkUnit, WorkUnitStatus
from .llm_gateway import LLMCallError


OutputT = TypeVar("OutputT")


class PreparedModelCall(Protocol[OutputT]):
    intent: object
    output_schema: type[OutputT]
    input_sha256s: tuple[str, ...]

    @property
    def work_unit_id(self) -> str: ...

    @property
    def reserved_capacity_tokens(self) -> int: ...

    async def invoke(self, gateway: object) -> OutputT: ...


class WorkRepository(Protocol):
    async def create(self, unit: WorkUnit) -> WorkUnit: ...
    async def get(self, execution_run_id: UUID, work_unit_id: str) -> WorkUnit | None: ...
    async def transition(self, execution_run_id: UUID, work_unit_id: str, change) -> WorkUnit: ...


class ArtifactLoader(Protocol):
    async def load(self, pointer: ArtifactPointer, schema: type[BaseModel]) -> BaseModel: ...


class DurableCallExecutionError(RuntimeError):
    """A persisted work unit cannot be executed automatically."""

    def __init__(self, message: str, *, work_unit_id: str) -> None:
        super().__init__(message)
        self.work_unit_id = work_unit_id


PersistOutput = Callable[[OutputT], Awaitable[ArtifactPointer]]


class DurableCallExecutor:
    """Lease, call, persist and checkpoint one paid model operation.

    A provider response is never requested again merely because persistence failed
    afterwards. Expired RUNNING units also remain blocked because their provider
    outcome is ambiguous and must be reconciled explicitly.
    """

    def __init__(
        self,
        *,
        gateway: object,
        artifacts: ArtifactLoader,
        work: WorkRepository,
        worker_id: str,
        engine_version: str,
        model_config_sha256: str,
        lease_ttl: timedelta = timedelta(minutes=15),
        work_max_attempts: int = 2,
    ) -> None:
        if not worker_id.strip() or not engine_version.strip():
            raise ValueError("durable execution requires worker and engine identities")
        if lease_ttl.total_seconds() <= 0:
            raise ValueError("durable execution requires a positive lease TTL")
        if not 1 <= work_max_attempts <= 8:
            raise ValueError("work attempt budget must be between one and eight")
        self._gateway = gateway
        self._artifacts = artifacts
        self._work = work
        self._worker_id = worker_id
        self._engine_version = engine_version
        self._model_config_sha256 = model_config_sha256
        self._lease_ttl = lease_ttl
        self._work_max_attempts = work_max_attempts

    async def execute(
        self,
        call: PreparedModelCall[OutputT],
        *,
        persist: PersistOutput[OutputT],
        input_uris: Sequence[str],
        dependencies: Sequence[str] = (),
    ) -> OutputT:
        intent = call.intent
        execution_run_id = cast(UUID, getattr(intent, "execution_run_id"))
        project_id = cast(UUID, getattr(intent, "project_id"))
        unit = await self._work.get(execution_run_id, call.work_unit_id)
        if unit is None:
            unit = WorkUnit(
                work_unit_id=call.work_unit_id,
                execution_run_id=execution_run_id,
                project_id=project_id,
                engine_version=self._engine_version,
                unit_type=cast(str, getattr(intent, "task_type")),
                dependencies=list(dependencies),
                input_uris=list(input_uris),
                input_sha256s=list(call.input_sha256s),
                prompt_version=cast(str, getattr(intent, "prompt_version")),
                model=cast(str, getattr(intent, "model")),
                model_config_sha256=self._model_config_sha256,
                cache_scope=f"run:{execution_run_id}",
                reserved_capacity_tokens=call.reserved_capacity_tokens,
                max_attempts=self._work_max_attempts,
            )
            await self._work.create(unit)
        else:
            self._validate_existing(unit, call, input_uris=input_uris, dependencies=dependencies)

        if unit.status is WorkUnitStatus.SUCCEEDED:
            pointer = ArtifactPointer(
                uri=cast(str, unit.output_uri),
                sha256=cast(str, unit.output_sha256),
            )
            loaded = await self._artifacts.load(pointer, cast(type[BaseModel], call.output_schema))
            return cast(OutputT, loaded)
        if unit.status is WorkUnitStatus.FAILED:
            raise DurableCallExecutionError(
                unit.error_message or "work unit failed terminally",
                work_unit_id=unit.work_unit_id,
            )

        now = datetime.now(UTC)
        await self._work.transition(
            execution_run_id,
            call.work_unit_id,
            lambda current: current.lease(owner=self._worker_id, now=now, ttl=self._lease_ttl),
        )
        await self._work.transition(
            execution_run_id,
            call.work_unit_id,
            lambda current: current.start(owner=self._worker_id, now=datetime.now(UTC)),
        )
        try:
            output = await call.invoke(self._gateway)
        except Exception as error:
            await self._work.transition(
                execution_run_id,
                call.work_unit_id,
                lambda current: current.fail(
                    owner=self._worker_id,
                    now=datetime.now(UTC),
                    error=error,
                    retryable=_retryable_model_failure(error),
                ),
            )
            raise

        try:
            pointer = await persist(output)
        except Exception as error:
            # The provider already returned successfully. A persistence retry must
            # never become a second paid generation with the same logical identity.
            await self._work.transition(
                execution_run_id,
                call.work_unit_id,
                lambda current: current.fail(
                    owner=self._worker_id,
                    now=datetime.now(UTC),
                    error=error,
                    retryable=False,
                ),
            )
            raise

        await self._work.transition(
            execution_run_id,
            call.work_unit_id,
            lambda current: current.succeed(
                owner=self._worker_id,
                now=datetime.now(UTC),
                output_uri=pointer.uri,
                output_sha256=pointer.sha256,
            ),
        )
        return output

    def _validate_existing(
        self,
        unit: WorkUnit,
        call: PreparedModelCall[OutputT],
        *,
        input_uris: Sequence[str],
        dependencies: Sequence[str],
    ) -> None:
        intent = call.intent
        expected = {
            "execution_run_id": getattr(intent, "execution_run_id"),
            "project_id": getattr(intent, "project_id"),
            "engine_version": self._engine_version,
            "unit_type": getattr(intent, "task_type"),
            "dependencies": list(dependencies),
            "input_uris": list(input_uris),
            "input_sha256s": list(call.input_sha256s),
            "prompt_version": getattr(intent, "prompt_version"),
            "model": getattr(intent, "model"),
            "model_config_sha256": self._model_config_sha256,
            "cache_scope": f"run:{getattr(intent, 'execution_run_id')}",
            "reserved_capacity_tokens": call.reserved_capacity_tokens,
        }
        actual = unit.model_dump(include=set(expected))
        if actual != expected:
            raise ValueError(f"persisted work identity conflicts with call {call.work_unit_id}")


def _retryable_model_failure(error: Exception) -> bool:
    if not isinstance(error, LLMCallError) or not error.attempts:
        return False
    final = error.attempts[-1]
    return final.status is LLMCallStatus.FAILED and final.retryable

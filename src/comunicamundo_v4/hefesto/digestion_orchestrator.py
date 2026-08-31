"""Resumable, bounded orchestration for graph-free Hefesto V2 digestion."""
from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from .contracts import NormalizedSource, PlanningRequest
from .digestion_contracts import (
    DIGESTION_ENGINE_VERSION,
    ArtifactPointer,
    DigestionArtifactRefs,
    DigestionExecutionRun,
    DigestionMode,
    DigestionPlan,
    DigestionRunResult,
    DigestionRunState,
    EditorialUnderstandingV2,
    EvidenceMatrix,
    SectionDigest,
    SourceDossier,
    SourceDossierPointer,
)
from .digestion_persistence import DigestionArtifactStore, ExecutionRunRepository
from .digestion_runtime import DigestionCallFactory, ModelGateway, PreparedDigestionCall
from .durable_calls import DurableCallExecutor, WorkRepository
from .execution_contracts import WorkUnitStatus
from .execution_scheduler import BoundedFairExecutor, ScheduledWork
from .execution_contracts import text_sha256
from .planning import fingerprint_brief
from .source_digestion import SourceDigestionPlanner, editorial_understanding_from_matrix


class DigestionExecutionError(RuntimeError):
    def __init__(self, message: str, failed_work_unit_ids: Sequence[str]) -> None:
        super().__init__(message)
        self.failed_work_unit_ids = tuple(failed_work_unit_ids)


class DigestionOrchestrator:
    """Runs independent units in bounded parallel phases and checkpoints every result."""

    def __init__(
        self,
        *,
        planner: SourceDigestionPlanner,
        calls: DigestionCallFactory,
        gateway: ModelGateway,
        executor: BoundedFairExecutor,
        artifacts: DigestionArtifactStore,
        runs: ExecutionRunRepository,
        work: WorkRepository,
        worker_id: str,
        lease_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        if not worker_id.strip() or lease_ttl.total_seconds() <= 0:
            raise ValueError("orchestrator requires a worker identity and positive lease TTL")
        self._planner = planner
        self._calls = calls
        self._executor = executor
        self._artifacts = artifacts
        self._runs = runs
        self._work = work
        self._durable = DurableCallExecutor(
            gateway=gateway,
            artifacts=artifacts,
            work=work,
            worker_id=worker_id,
            engine_version=DIGESTION_ENGINE_VERSION,
            model_config_sha256=calls.config.config_sha256,
            lease_ttl=lease_ttl,
            work_max_attempts=2,
        )

    async def start(self, request: PlanningRequest) -> DigestionRunResult:
        plan = self._planner.plan(project_id=request.project_id, sources=request.sources)
        request_pointer = await self._artifacts.persist_request(plan, request)
        plan_pointer = await self._artifacts.persist_plan(plan)
        run = DigestionExecutionRun(
            execution_run_id=plan.execution_run_id,
            project_id=plan.project_id,
            source_fingerprint=plan.source_fingerprint,
            brief_fingerprint=fingerprint_brief(request.brief),
            state=DigestionRunState.PLANNED,
            source_count=len(plan.sources),
            planned_model_calls=plan.planned_model_calls,
            artifacts=DigestionArtifactRefs(request=request_pointer, plan=plan_pointer),
        )
        await self._runs.create(run)
        return await self._advance(run, plan, request)

    async def resume(self, execution_run_id: UUID) -> DigestionRunResult:
        run = await self._runs.get(execution_run_id)
        if run is None:
            raise KeyError(f"unknown digestion execution run {execution_run_id}")
        plan = await self._artifacts.load(run.artifacts.plan, DigestionPlan)
        request = await self._artifacts.load(run.artifacts.request, PlanningRequest)
        if plan.execution_run_id != run.execution_run_id or request.project_id != run.project_id:
            raise ValueError("persisted digestion inputs disagree with the execution run")
        return await self._advance(run, plan, request)

    async def _advance(
        self,
        run: DigestionExecutionRun,
        plan: DigestionPlan,
        request: PlanningRequest,
    ) -> DigestionRunResult:
        if run.state is DigestionRunState.UNDERSTANDING_READY:
            return await self._load_result(run, plan)
        run = await self._runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=DigestionRunState.DIGESTING,
                failed_work_unit_ids=[],
                error_class=None,
                error_message=None,
            ),
        )
        sources = _ordered_sources(plan, request.sources)
        input_uris = [run.artifacts.request.uri, run.artifacts.plan.uri]

        initial_calls: list[PreparedDigestionCall] = []
        for source, source_plan in zip(sources, plan.sources, strict=True):
            if source_plan.mode is DigestionMode.DIRECT:
                initial_calls.append(
                    self._calls.direct_dossier(
                        plan=plan,
                        source=source,
                        source_plan=source_plan,
                        brief=request.brief,
                    )
                )
            else:
                initial_calls.extend(
                    self._calls.section_digest(
                        plan=plan,
                        source=source,
                        source_plan=source_plan,
                        section_ordinal=section.ordinal,
                    )
                    for section in source_plan.sections
                )

        initial = await self._execute_phase(run, plan, initial_calls, input_uris=input_uris)
        direct_dossiers = {
            value.source_id: value for value in initial.values() if isinstance(value, SourceDossier)
        }
        section_digests = [value for value in initial.values() if isinstance(value, SectionDigest)]

        reducer_calls: list[PreparedDigestionCall] = []
        for source, source_plan in zip(sources, plan.sources, strict=True):
            if source_plan.mode is DigestionMode.HIERARCHICAL:
                source_digests = [item for item in section_digests if item.source_id == source.source_id]
                reducer_calls.append(
                    self._calls.reduced_dossier(
                        plan=plan,
                        source=source,
                        source_plan=source_plan,
                        section_digests=source_digests,
                        brief=request.brief,
                    )
                )
        reduced = await self._execute_phase(
            run,
            plan,
            reducer_calls,
            input_uris=input_uris,
            dependencies=[call.work_unit_id for call in initial_calls],
        )
        dossiers_by_source = {
            **direct_dossiers,
            **{value.source_id: value for value in reduced.values() if isinstance(value, SourceDossier)},
        }
        dossiers = [dossiers_by_source[item.source_id] for item in plan.sources]
        section_pointers = await self._pointers(initial_calls, only_schema=SectionDigest)
        dossier_pointers = [
            SourceDossierPointer(
                source_id=dossier.source_id,
                **(await self._pointer_for_work(plan.execution_run_id, _dossier_call_id(dossier, initial_calls, reducer_calls))).model_dump(),
            )
            for dossier in dossiers
        ]
        completed_before_matrix = len(initial_calls) + len(reducer_calls)
        run = await self._runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=DigestionRunState.DOSSIERS_READY,
                completed_model_calls=completed_before_matrix,
                artifacts=current.artifacts.model_copy(
                    update={
                        "section_digests": section_pointers,
                        "source_dossiers": dossier_pointers,
                    }
                ),
            ),
        )

        matrix_call = self._calls.evidence_matrix(plan=plan, dossiers=dossiers, brief=request.brief)
        matrix_values = await self._execute_phase(
            run,
            plan,
            [matrix_call],
            input_uris=[*input_uris, *[item.uri for item in dossier_pointers]],
            dependencies=[call.work_unit_id for call in [*initial_calls, *reducer_calls]],
        )
        matrix = cast(EvidenceMatrix, matrix_values[matrix_call.work_unit_id])
        matrix_pointer = await self._pointer_for_work(plan.execution_run_id, matrix_call.work_unit_id)
        understanding = editorial_understanding_from_matrix(matrix)
        understanding_pointer = await self._artifacts.persist_understanding(plan, understanding)
        run = await self._runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=DigestionRunState.UNDERSTANDING_READY,
                completed_model_calls=plan.planned_model_calls,
                artifacts=current.artifacts.model_copy(
                    update={
                        "evidence_matrix": matrix_pointer,
                        "editorial_understanding": understanding_pointer,
                    }
                ),
            ),
        )
        return DigestionRunResult(
            execution_run=run,
            digestion_plan=plan,
            source_dossiers=dossiers,
            evidence_matrix=matrix,
            editorial_understanding=understanding,
        )

    async def _execute_phase(
        self,
        run: DigestionExecutionRun,
        plan: DigestionPlan,
        calls: Sequence[PreparedDigestionCall],
        *,
        input_uris: list[str],
        dependencies: list[str] | None = None,
    ) -> dict[str, object]:
        if not calls:
            return {}
        scheduled = [
            ScheduledWork(
                work_unit_id=call.work_unit_id,
                project_id=run.project_id,
                reserved_capacity_tokens=call.reserved_capacity_tokens,
                operation=lambda item=call: self._execute_call(
                    plan,
                    item,
                    input_uris=input_uris,
                    dependencies=dependencies or [],
                ),
            )
            for call in calls
        ]
        outcomes = await self._executor.run_all(scheduled)
        failures = [item for item in outcomes if item.error is not None]
        if failures:
            failed_ids = [item.work_unit_id for item in failures]
            first_error = cast(Exception, failures[0].error)
            await self._runs.transition(
                run.execution_run_id,
                lambda current: _updated_run(
                    current,
                    state=DigestionRunState.FAILED,
                    failed_work_unit_ids=failed_ids,
                    error_class=type(first_error).__name__,
                    error_message=str(first_error)[:4000],
                ),
            )
            raise DigestionExecutionError(str(first_error), failed_ids) from first_error
        return {item.work_unit_id: item.value for item in outcomes}

    async def _execute_call(
        self,
        plan: DigestionPlan,
        call: PreparedDigestionCall,
        *,
        input_uris: list[str],
        dependencies: list[str],
    ) -> object:
        return await self._durable.execute(
            call,
            persist=lambda output: self._persist_output(plan, output),
            input_uris=input_uris,
            dependencies=dependencies,
        )

    async def _persist_output(self, plan: DigestionPlan, output: object) -> ArtifactPointer:
        if isinstance(output, SectionDigest):
            return await self._artifacts.persist_section_digest(plan, output)
        if isinstance(output, SourceDossier):
            return await self._artifacts.persist_source_dossier(plan, output)
        if isinstance(output, EvidenceMatrix):
            return await self._artifacts.persist_evidence_matrix(plan, output)
        raise TypeError(f"unsupported digestion output: {type(output).__name__}")

    async def _pointer_for_work(self, execution_run_id: UUID, work_unit_id: str) -> ArtifactPointer:
        unit = await self._work.get(execution_run_id, work_unit_id)
        if unit is None or unit.status is not WorkUnitStatus.SUCCEEDED:
            raise ValueError(f"work unit has no persisted output: {work_unit_id}")
        return ArtifactPointer(uri=cast(str, unit.output_uri), sha256=cast(str, unit.output_sha256))

    async def _pointers(
        self,
        calls: Sequence[PreparedDigestionCall],
        *,
        only_schema: type,
    ) -> list[ArtifactPointer]:
        return [
            await self._pointer_for_work(call.intent.execution_run_id, call.work_unit_id)
            for call in calls
            if call.output_schema is only_schema
        ]

    async def _load_result(self, run: DigestionExecutionRun, plan: DigestionPlan) -> DigestionRunResult:
        dossiers = [
            await self._artifacts.load(pointer, SourceDossier)
            for pointer in run.artifacts.source_dossiers
        ]
        if run.artifacts.evidence_matrix is None or run.artifacts.editorial_understanding is None:
            raise ValueError("ready run is missing final artifacts")
        matrix = await self._artifacts.load(run.artifacts.evidence_matrix, EvidenceMatrix)
        understanding = await self._artifacts.load(
            run.artifacts.editorial_understanding,
            EditorialUnderstandingV2,
        )
        return DigestionRunResult(
            execution_run=run,
            digestion_plan=plan,
            source_dossiers=dossiers,
            evidence_matrix=matrix,
            editorial_understanding=understanding,
        )


def _ordered_sources(plan: DigestionPlan, sources: Sequence[NormalizedSource]) -> list[NormalizedSource]:
    source_map = {item.source_id: item for item in sources}
    if len(source_map) != len(sources) or set(source_map) != {item.source_id for item in plan.sources}:
        raise ValueError("request sources disagree with the persisted digestion plan")
    return [source_map[item.source_id] for item in plan.sources]


def _dossier_call_id(
    dossier: SourceDossier,
    initial: Sequence[PreparedDigestionCall],
    reducers: Sequence[PreparedDigestionCall],
) -> str:
    candidates = [
        call
        for call in [*initial, *reducers]
        if call.output_schema is SourceDossier
        and text_sha256(str(dossier.source_id)) in call.input_sha256s
    ]
    if len(candidates) != 1:
        raise ValueError(f"cannot identify persisted dossier work for source {dossier.source_id}")
    return candidates[0].work_unit_id


def _updated_run(run: DigestionExecutionRun, **updates) -> DigestionExecutionRun:
    return DigestionExecutionRun.model_validate(
        {**run.model_dump(), **updates, "updated_at": datetime.now(UTC)}
    )

"""Resumable Narrative Axis and Editorial Project gates for Hefesto V2."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from .axis_proposal import apply_evidence_strength_review, validate_axis_statement
from .contracts import (
    EditorialProject,
    EditorialProjectProposal,
    EditorialSubject,
    NarrativeAxis,
    NarrativeAxisEvidenceReview,
    NarrativeAxisProposal,
    PlanningRequest,
)
from .digestion_contracts import (
    ArtifactPointer,
    DigestionExecutionRun,
    DigestionPlan,
    DigestionRunState,
    EditorialUnderstandingV2,
    EvidenceMatrix,
)
from .digestion_persistence import DigestionArtifactStore, ExecutionRunRepository
from .durable_calls import DurableCallExecutor, WorkRepository
from .editorial_v2_contracts import (
    EDITORIAL_V2_ENGINE_VERSION,
    EditorialPlanningV2Result,
    EditorialProjectRecordV2,
    EditorialV2ArtifactRefs,
    EditorialV2ExecutionRun,
    EditorialV2State,
    HefestoTipV2,
    NarrativeAxisRecordV2,
    approve_axis_record,
    approve_project_record_without_tips,
)
from .editorial_v2_persistence import EditorialV2ArtifactStore, EditorialV2RunRepository
from .editorial_v2_runtime import (
    EditorialV2CallFactory,
    ModelGateway,
    PreparedEditorialCall,
)
from .execution_contracts import WorkUnitStatus
from .execution_scheduler import BoundedFairExecutor, ScheduledWork
from .hefesto_tips import HefestoTipTimingResult


@dataclass(frozen=True, slots=True)
class EditorialV2Context:
    digestion_run: DigestionExecutionRun
    request: PlanningRequest
    plan: DigestionPlan
    matrix: EvidenceMatrix
    understanding: EditorialUnderstandingV2


class EditorialV2Orchestrator:
    """Expose explicit human gates while keeping every paid call resumable."""

    def __init__(
        self,
        *,
        calls: EditorialV2CallFactory,
        gateway: ModelGateway,
        digestion_artifacts: DigestionArtifactStore,
        digestion_runs: ExecutionRunRepository,
        editorial_artifacts: EditorialV2ArtifactStore,
        editorial_runs: EditorialV2RunRepository,
        work: WorkRepository,
        executor: BoundedFairExecutor,
        worker_id: str,
        lease_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self._calls = calls
        self._digestion_artifacts = digestion_artifacts
        self._digestion_runs = digestion_runs
        self._editorial_artifacts = editorial_artifacts
        self._editorial_runs = editorial_runs
        self._work = work
        self._executor = executor
        self._durable = DurableCallExecutor(
            gateway=gateway,
            artifacts=editorial_artifacts,
            work=work,
            worker_id=worker_id,
            engine_version=EDITORIAL_V2_ENGINE_VERSION,
            model_config_sha256=calls.config.config_sha256,
            lease_ttl=lease_ttl,
            work_max_attempts=2,
        )

    async def propose_axis(self, execution_run_id: UUID) -> EditorialPlanningV2Result:
        context, run = await self._load_context(execution_run_id)
        if _at_or_after(run.state, EditorialV2State.AXIS_PROPOSED):
            axis = await self._load_axis_for_state(run)
            project = await self._load_project_for_state(run)
            return EditorialPlanningV2Result(
                execution_run=run,
                narrative_axis_record=axis,
                editorial_project_record=project,
            )

        candidate_call = self._calls.propose_axis(
            execution_run_id=run.execution_run_id,
            project_id=run.project_id,
            brief=context.request.brief,
            understanding=context.understanding,
            matrix=context.matrix,
            understanding_sha256=run.artifacts.editorial_understanding.sha256,
            matrix_sha256=run.artifacts.evidence_matrix.sha256,
        )
        work_ids = [candidate_call.work_unit_id]
        try:
            candidate, run = await self._ensure_call(
                run,
                candidate_call,
                pointer_field="axis_candidate",
                persist=lambda value: self._editorial_artifacts.persist_axis_candidate(
                    run.execution_run_id, run.project_id, value
                ),
                input_uris=self._base_input_uris(run),
            )
            candidate = cast(NarrativeAxisProposal, candidate)
            candidate = candidate.model_copy(update={"statement": validate_axis_statement(candidate.statement)})
            review_call = self._calls.review_axis(
                execution_run_id=run.execution_run_id,
                project_id=run.project_id,
                candidate=candidate,
                understanding=context.understanding,
                matrix=context.matrix,
                understanding_sha256=run.artifacts.editorial_understanding.sha256,
                matrix_sha256=run.artifacts.evidence_matrix.sha256,
            )
            work_ids.append(review_call.work_unit_id)
            review, run = await self._ensure_call(
                run,
                review_call,
                pointer_field="axis_evidence_review",
                persist=lambda value: self._editorial_artifacts.persist_axis_review(
                    run.execution_run_id, run.project_id, value
                ),
                input_uris=[*self._base_input_uris(run), cast(ArtifactPointer, run.artifacts.axis_candidate).uri],
                dependencies=[candidate_call.work_unit_id],
            )
            review = cast(NarrativeAxisEvidenceReview, review)
            if review.candidate_statement != candidate.statement:
                raise ValueError("evidence reviewer changed the candidate under review")
            final_statement = apply_evidence_strength_review(candidate.statement, review)
            existing = await self._editorial_artifacts.find_axis_record(
                run.execution_run_id, run.project_id, approved=False
            )
            if existing is None:
                axis = NarrativeAxis(
                    project_id=run.project_id,
                    statement=final_statement,
                )
                axis_record = NarrativeAxisRecordV2(
                    execution_run_id=run.execution_run_id,
                    project_id=run.project_id,
                    understanding_id=context.understanding.understanding_id,
                    evidence_matrix_id=context.matrix.evidence_matrix_id,
                    evidence_matrix_sha256=run.artifacts.evidence_matrix.sha256,
                    model=self._calls.config.model,
                    candidate=candidate,
                    evidence_review=review,
                    narrative_axis=axis,
                )
                axis_pointer = await self._editorial_artifacts.persist_axis_record(
                    run.execution_run_id,
                    run.project_id,
                    axis_record,
                    approved=False,
                )
            else:
                axis_pointer, axis_record = existing
                if axis_record.narrative_axis.statement != final_statement:
                    raise ValueError("persisted proposed Axis conflicts with completed model work")
            run = await self._editorial_runs.transition(
                run.execution_run_id,
                lambda current: _updated_run(
                    current,
                    state=EditorialV2State.AXIS_PROPOSED,
                    active_narrative_axis_id=axis_record.narrative_axis.narrative_axis_id,
                    artifacts=current.artifacts.model_copy(update={"proposed_axis": axis_pointer}),
                ),
            )
        except Exception as error:
            await self._fail_current(run.execution_run_id, error, work_ids)
            raise
        return EditorialPlanningV2Result(
            execution_run=run,
            narrative_axis_record=axis_record,
        )

    async def approve_axis_and_propose_project(
        self,
        execution_run_id: UUID,
    ) -> EditorialPlanningV2Result:
        await self.propose_axis(execution_run_id)
        context, run = await self._load_context(execution_run_id)
        if _at_or_after(run.state, EditorialV2State.PROJECT_PROPOSED):
            return EditorialPlanningV2Result(
                execution_run=run,
                narrative_axis_record=await self._load_axis_for_state(run),
                editorial_project_record=await self._load_project_for_state(run),
            )

        proposed_axis = await self._editorial_artifacts.load(
            cast(ArtifactPointer, run.artifacts.proposed_axis), NarrativeAxisRecordV2
        )
        if run.state is EditorialV2State.AXIS_PROPOSED:
            existing = await self._editorial_artifacts.find_axis_record(
                run.execution_run_id, run.project_id, approved=True
            )
            if existing is None:
                approved_axis = approve_axis_record(proposed_axis)
                approved_axis_pointer = await self._editorial_artifacts.persist_axis_record(
                    run.execution_run_id,
                    run.project_id,
                    approved_axis,
                    approved=True,
                )
            else:
                approved_axis_pointer, approved_axis = existing
            run = await self._editorial_runs.transition(
                run.execution_run_id,
                lambda current: _updated_run(
                    current,
                    state=EditorialV2State.AXIS_APPROVED,
                    artifacts=current.artifacts.model_copy(update={"approved_axis": approved_axis_pointer}),
                ),
            )
        else:
            approved_axis = await self._editorial_artifacts.load(
                cast(ArtifactPointer, run.artifacts.approved_axis), NarrativeAxisRecordV2
            )

        proposal_call = self._calls.propose_project(
            execution_run_id=run.execution_run_id,
            project_id=run.project_id,
            axis=approved_axis.narrative_axis.statement,
            brief=context.request.brief,
            understanding=context.understanding,
            matrix=context.matrix,
            understanding_sha256=run.artifacts.editorial_understanding.sha256,
            matrix_sha256=run.artifacts.evidence_matrix.sha256,
        )
        work_ids = [proposal_call.work_unit_id]
        try:
            proposal, run = await self._ensure_call(
                run,
                proposal_call,
                pointer_field="project_proposal",
                persist=lambda value: self._editorial_artifacts.persist_project_proposal(
                    run.execution_run_id, run.project_id, value
                ),
                input_uris=[*self._base_input_uris(run), cast(ArtifactPointer, run.artifacts.approved_axis).uri],
            )
            proposal = cast(EditorialProjectProposal, proposal)
            timing, run = await self._ensure_tip_timing(
                run,
                context,
                proposal,
                proposal_call,
                work_ids,
            )
            if len(timing.timings) != len(proposal.llm_subjects):
                raise ValueError("Dicas timing count does not match the proposed tips")
            generated_tips = [
                HefestoTipV2(
                    text=tip,
                    incremental_minutes=estimate.incremental_minutes,
                    display_text=f"{tip.strip()} — +~{estimate.incremental_minutes} min",
                )
                for tip, estimate in zip(proposal.llm_subjects, timing.timings, strict=True)
            ]
            existing_project = await self._editorial_artifacts.find_project_record(
                run.execution_run_id, run.project_id, approved=False
            )
            if existing_project is None:
                project = EditorialProject(
                    project_id=run.project_id,
                    narrative_axis_id=approved_axis.narrative_axis.narrative_axis_id,
                    narrative_axis=approved_axis.narrative_axis.statement,
                    brief=context.request.brief,
                    working_title=proposal.working_title,
                    editorial_promise=proposal.editorial_promise,
                    core_subjects=[
                        EditorialSubject(**subject.model_dump()) for subject in proposal.core_subjects
                    ],
                    llm_subjects=[tip.display_text for tip in generated_tips],
                    enrichment_opportunities=proposal.enrichment_opportunities,
                )
                project_record = EditorialProjectRecordV2(
                    execution_run_id=run.execution_run_id,
                    project_id=run.project_id,
                    understanding_id=context.understanding.understanding_id,
                    narrative_axis_id=approved_axis.narrative_axis.narrative_axis_id,
                    model=self._calls.config.model,
                    proposal=proposal,
                    generated_tips=generated_tips,
                    accepted_tips=[],
                    editorial_project=project,
                )
                project_pointer = await self._editorial_artifacts.persist_project_record(
                    run.execution_run_id,
                    run.project_id,
                    project_record,
                    approved=False,
                )
            else:
                project_pointer, project_record = existing_project
            run = await self._editorial_runs.transition(
                run.execution_run_id,
                lambda current: _updated_run(
                    current,
                    state=EditorialV2State.PROJECT_PROPOSED,
                    active_editorial_project_id=project_record.editorial_project.editorial_project_id,
                    artifacts=current.artifacts.model_copy(update={"proposed_project": project_pointer}),
                ),
            )
        except Exception as error:
            await self._fail_current(run.execution_run_id, error, work_ids)
            raise
        return EditorialPlanningV2Result(
            execution_run=run,
            narrative_axis_record=approved_axis,
            editorial_project_record=project_record,
        )

    async def approve_project_without_tips(self, execution_run_id: UUID) -> EditorialPlanningV2Result:
        await self.approve_axis_and_propose_project(execution_run_id)
        _, run = await self._load_context(execution_run_id)
        axis = await self._load_axis_for_state(run)
        proposed = await self._editorial_artifacts.load(
            cast(ArtifactPointer, run.artifacts.proposed_project), EditorialProjectRecordV2
        )
        if run.state is EditorialV2State.PROJECT_APPROVED:
            approved = await self._load_project_for_state(run)
            return EditorialPlanningV2Result(
                execution_run=run,
                narrative_axis_record=axis,
                editorial_project_record=approved,
            )
        existing = await self._editorial_artifacts.find_project_record(
            run.execution_run_id, run.project_id, approved=True
        )
        if existing is None:
            approved = approve_project_record_without_tips(proposed)
            approved_pointer = await self._editorial_artifacts.persist_project_record(
                run.execution_run_id,
                run.project_id,
                approved,
                approved=True,
            )
        else:
            approved_pointer, approved = existing
        run = await self._editorial_runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=EditorialV2State.PROJECT_APPROVED,
                artifacts=current.artifacts.model_copy(update={"approved_project": approved_pointer}),
            ),
        )
        return EditorialPlanningV2Result(
            execution_run=run,
            narrative_axis_record=axis,
            editorial_project_record=approved,
        )

    async def _load_context(
        self, execution_run_id: UUID
    ) -> tuple[EditorialV2Context, EditorialV2ExecutionRun]:
        digestion = await self._digestion_runs.get(execution_run_id)
        if digestion is None:
            raise KeyError(f"unknown digestion execution run {execution_run_id}")
        if digestion.state is not DigestionRunState.UNDERSTANDING_READY:
            raise ValueError("editorial gates require an understanding_ready digestion run")
        if digestion.artifacts.evidence_matrix is None or digestion.artifacts.editorial_understanding is None:
            raise ValueError("digestion run is missing matrix or understanding artifacts")
        request = await self._digestion_artifacts.load(digestion.artifacts.request, PlanningRequest)
        plan = await self._digestion_artifacts.load(digestion.artifacts.plan, DigestionPlan)
        matrix = await self._digestion_artifacts.load(digestion.artifacts.evidence_matrix, EvidenceMatrix)
        understanding = await self._digestion_artifacts.load(
            digestion.artifacts.editorial_understanding, EditorialUnderstandingV2
        )
        context = EditorialV2Context(
            digestion_run=digestion,
            request=request,
            plan=plan,
            matrix=matrix,
            understanding=understanding,
        )
        run = await self._editorial_runs.get(execution_run_id)
        if run is None:
            run = EditorialV2ExecutionRun(
                execution_run_id=digestion.execution_run_id,
                project_id=digestion.project_id,
                source_fingerprint=digestion.source_fingerprint,
                brief_fingerprint=digestion.brief_fingerprint,
                state=EditorialV2State.UNDERSTANDING_READY,
                artifacts=EditorialV2ArtifactRefs(
                    request=digestion.artifacts.request,
                    digestion_plan=digestion.artifacts.plan,
                    evidence_matrix=digestion.artifacts.evidence_matrix,
                    editorial_understanding=digestion.artifacts.editorial_understanding,
                ),
            )
            await self._editorial_runs.create(run)
        else:
            self._validate_identity(run, digestion)
            if run.state is EditorialV2State.FAILED:
                run = await self._editorial_runs.transition(
                    execution_run_id,
                    lambda current: _updated_run(
                        current,
                        state=cast(EditorialV2State, current.resume_state),
                        resume_state=None,
                        failed_work_unit_ids=[],
                        error_class=None,
                        error_message=None,
                    ),
                )
        return context, run

    async def _ensure_call(
        self,
        run: EditorialV2ExecutionRun,
        call: PreparedEditorialCall,
        *,
        pointer_field: str,
        persist,
        input_uris: list[str],
        dependencies: list[str] | None = None,
    ) -> tuple[object, EditorialV2ExecutionRun]:
        pointer = getattr(run.artifacts, pointer_field)
        if pointer is not None:
            return await self._editorial_artifacts.load(pointer, call.output_schema), run
        outcomes = await self._executor.run_all(
            [
                ScheduledWork(
                    work_unit_id=call.work_unit_id,
                    project_id=run.project_id,
                    reserved_capacity_tokens=call.reserved_capacity_tokens,
                    operation=lambda: self._durable.execute(
                        call,
                        persist=persist,
                        input_uris=input_uris,
                        dependencies=dependencies or [],
                    ),
                )
            ]
        )
        outcome = outcomes[0]
        if outcome.error is not None:
            raise outcome.error
        value = outcome.value
        pointer = await self._pointer_for_work(run.execution_run_id, call.work_unit_id)
        run = await self._editorial_runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                completed_model_calls=current.completed_model_calls + 1,
                artifacts=current.artifacts.model_copy(update={pointer_field: pointer}),
            ),
        )
        return value, run

    async def _ensure_tip_timing(
        self,
        run: EditorialV2ExecutionRun,
        context: EditorialV2Context,
        proposal: EditorialProjectProposal,
        proposal_call: PreparedEditorialCall,
        work_ids: list[str],
    ) -> tuple[HefestoTipTimingResult, EditorialV2ExecutionRun]:
        if run.artifacts.tip_timing is not None:
            timing = await self._editorial_artifacts.load(
                run.artifacts.tip_timing, HefestoTipTimingResult
            )
            return timing, run
        if not proposal.llm_subjects:
            timing = HefestoTipTimingResult()
            pointer = await self._editorial_artifacts.persist_tip_timing(
                run.execution_run_id, run.project_id, timing
            )
            run = await self._editorial_runs.transition(
                run.execution_run_id,
                lambda current: _updated_run(
                    current,
                    artifacts=current.artifacts.model_copy(update={"tip_timing": pointer}),
                ),
            )
            return timing, run
        timing_call = self._calls.time_tips(
            execution_run_id=run.execution_run_id,
            project_id=run.project_id,
            brief=context.request.brief,
            proposal=proposal,
        )
        work_ids.append(timing_call.work_unit_id)
        timing, run = await self._ensure_call(
            run,
            timing_call,
            pointer_field="tip_timing",
            persist=lambda value: self._editorial_artifacts.persist_tip_timing(
                run.execution_run_id, run.project_id, value
            ),
            input_uris=[*self._base_input_uris(run), cast(ArtifactPointer, run.artifacts.project_proposal).uri],
            dependencies=[proposal_call.work_unit_id],
        )
        return cast(HefestoTipTimingResult, timing), run

    async def _load_axis_for_state(self, run: EditorialV2ExecutionRun) -> NarrativeAxisRecordV2:
        pointer = (
            run.artifacts.approved_axis
            if _at_or_after(run.state, EditorialV2State.AXIS_APPROVED)
            else run.artifacts.proposed_axis
        )
        if pointer is None:
            raise ValueError("editorial run has no Axis artifact for its state")
        return await self._editorial_artifacts.load(pointer, NarrativeAxisRecordV2)

    async def _load_project_for_state(
        self, run: EditorialV2ExecutionRun
    ) -> EditorialProjectRecordV2 | None:
        if not _at_or_after(run.state, EditorialV2State.PROJECT_PROPOSED):
            return None
        pointer = (
            run.artifacts.approved_project
            if _at_or_after(run.state, EditorialV2State.PROJECT_APPROVED)
            else run.artifacts.proposed_project
        )
        if pointer is None:
            raise ValueError("editorial run has no Project artifact for its state")
        return await self._editorial_artifacts.load(pointer, EditorialProjectRecordV2)

    async def _pointer_for_work(self, execution_run_id: UUID, work_unit_id: str) -> ArtifactPointer:
        unit = await self._work.get(execution_run_id, work_unit_id)
        if unit is None or unit.status is not WorkUnitStatus.SUCCEEDED:
            raise ValueError(f"work unit has no persisted output: {work_unit_id}")
        return ArtifactPointer(uri=cast(str, unit.output_uri), sha256=cast(str, unit.output_sha256))

    async def _fail_current(self, execution_run_id: UUID, error: Exception, work_ids: list[str]) -> None:
        current = await self._editorial_runs.get(execution_run_id)
        if current is None or current.state is EditorialV2State.FAILED:
            return
        failed_ids = []
        for work_unit_id in dict.fromkeys(work_ids):
            unit = await self._work.get(execution_run_id, work_unit_id)
            if unit is None or unit.status is not WorkUnitStatus.SUCCEEDED:
                failed_ids.append(work_unit_id)
        if not failed_ids:
            # A failure after every paid output was checkpointed can be recovered
            # from those work units; do not mislabel successful calls as failed.
            return
        await self._editorial_runs.transition(
            execution_run_id,
            lambda value: _updated_run(
                value,
                state=EditorialV2State.FAILED,
                resume_state=value.state,
                failed_work_unit_ids=failed_ids,
                error_class=type(error).__name__,
                error_message=str(error)[:4000],
            ),
        )

    @staticmethod
    def _base_input_uris(run: EditorialV2ExecutionRun) -> list[str]:
        return [
            run.artifacts.request.uri,
            run.artifacts.digestion_plan.uri,
            run.artifacts.evidence_matrix.uri,
            run.artifacts.editorial_understanding.uri,
        ]

    @staticmethod
    def _validate_identity(run: EditorialV2ExecutionRun, digestion: DigestionExecutionRun) -> None:
        if (
            run.project_id != digestion.project_id
            or run.source_fingerprint != digestion.source_fingerprint
            or run.brief_fingerprint != digestion.brief_fingerprint
            or run.artifacts.request != digestion.artifacts.request
            or run.artifacts.digestion_plan != digestion.artifacts.plan
            or run.artifacts.evidence_matrix != digestion.artifacts.evidence_matrix
            or run.artifacts.editorial_understanding != digestion.artifacts.editorial_understanding
        ):
            raise ValueError("editorial execution identity conflicts with its digestion run")


def _at_or_after(current: EditorialV2State, target: EditorialV2State) -> bool:
    if current is EditorialV2State.FAILED:
        return False
    order = [
        EditorialV2State.UNDERSTANDING_READY,
        EditorialV2State.AXIS_PROPOSED,
        EditorialV2State.AXIS_APPROVED,
        EditorialV2State.PROJECT_PROPOSED,
        EditorialV2State.PROJECT_APPROVED,
    ]
    return order.index(current) >= order.index(target)


def _updated_run(run: EditorialV2ExecutionRun, **updates) -> EditorialV2ExecutionRun:
    return EditorialV2ExecutionRun.model_validate(
        {**run.model_dump(), **updates, "updated_at": datetime.now(UTC)}
    )

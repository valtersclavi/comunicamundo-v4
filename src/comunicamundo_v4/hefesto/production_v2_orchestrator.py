"""Resumable, bounded full-article production for Hefesto V2."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import cast
from uuid import UUID

from .contracts import EditorialReview, FinalArticle, NarrativePlan
from .digestion_contracts import (
    ArtifactPointer,
    DigestionExecutionRun,
    DigestionRunState,
    EvidenceMatrix,
    SourceDossier,
)
from .digestion_persistence import DigestionArtifactStore, ExecutionRunRepository
from .durable_calls import DurableCallExecutor, WorkRepository
from .editorial_v2_contracts import (
    EditorialProjectRecordV2,
    EditorialV2ExecutionRun,
    EditorialV2State,
)
from .editorial_v2_persistence import EditorialV2ArtifactStore, EditorialV2RunRepository
from .execution_contracts import WorkUnitStatus
from .execution_scheduler import BoundedFairExecutor, ScheduledWork
from .production_evidence import ProductionEvidenceBuilder
from .production_v2_contracts import (
    PRODUCTION_V2_ENGINE_VERSION,
    ArticleDraftV2,
    ProductionArtifactRefsV2,
    ProductionEvidenceBundle,
    ProductionExecutionRunV2,
    ProductionRecordV2,
    ProductionV2Result,
    ProductionV2State,
    SubjectResearchBundleProposal,
)
from .production_v2_persistence import ProductionV2ArtifactStore, ProductionV2RunRepository
from .production_v2_runtime import (
    ModelGateway,
    PreparedProductionCall,
    ProductionV2CallFactory,
)


class ProductionV2Orchestrator:
    def __init__(
        self,
        *,
        evidence_builder: ProductionEvidenceBuilder,
        calls: ProductionV2CallFactory,
        gateway: ModelGateway,
        executor: BoundedFairExecutor,
        digestion_artifacts: DigestionArtifactStore,
        digestion_runs: ExecutionRunRepository,
        editorial_artifacts: EditorialV2ArtifactStore,
        editorial_runs: EditorialV2RunRepository,
        production_artifacts: ProductionV2ArtifactStore,
        production_runs: ProductionV2RunRepository,
        work: WorkRepository,
        worker_id: str,
        lease_ttl: timedelta = timedelta(minutes=15),
    ) -> None:
        self._evidence_builder = evidence_builder
        self._calls = calls
        self._executor = executor
        self._digestion_artifacts = digestion_artifacts
        self._digestion_runs = digestion_runs
        self._editorial_artifacts = editorial_artifacts
        self._editorial_runs = editorial_runs
        self._production_artifacts = production_artifacts
        self._production_runs = production_runs
        self._work = work
        self._durable = DurableCallExecutor(
            gateway=gateway,
            artifacts=production_artifacts,
            work=work,
            worker_id=worker_id,
            engine_version=PRODUCTION_V2_ENGINE_VERSION,
            model_config_sha256=calls.config.config_sha256,
            lease_ttl=lease_ttl,
            work_max_attempts=2,
        )

    async def run(self, execution_run_id: UUID) -> ProductionV2Result:
        digestion, editorial, project_record, run, matrix, dossiers = await self._context(
            execution_run_id
        )
        if run.state is ProductionV2State.ARTICLE_READY:
            record = await self._production_artifacts.load(
                cast(ArtifactPointer, run.artifacts.production_record), ProductionRecordV2
            )
            return ProductionV2Result(execution_run=run, production_record=record)

        project = project_record.editorial_project
        work_ids: list[str] = []
        try:
            evidence, run = await self._ensure_evidence(run, project, matrix, dossiers)

            research_call = self._calls.research(
                execution_run_id=run.execution_run_id,
                project=project,
                evidence=evidence,
                approved_project_sha256=run.artifacts.approved_project.sha256,
            )
            work_ids.append(research_call.work_unit_id)
            research, run = await self._ensure_single_call(
                run,
                research_call,
                pointer_field="research_bundle",
                next_state=ProductionV2State.RESEARCH_READY,
                persist=lambda value: self._production_artifacts.persist_research(
                    run.execution_run_id, run.project_id, value
                ),
                input_uris=self._base_input_uris(run),
            )
            research = cast(SubjectResearchBundleProposal, research)

            architecture_call = self._calls.architecture(
                execution_run_id=run.execution_run_id,
                project=project,
                research=research,
                evidence=evidence,
            )
            work_ids.append(architecture_call.work_unit_id)
            architecture, run = await self._ensure_single_call(
                run,
                architecture_call,
                pointer_field="narrative_plan",
                next_state=ProductionV2State.ARCHITECTURE_READY,
                persist=lambda value: self._production_artifacts.persist_architecture(
                    run.execution_run_id, run.project_id, value
                ),
                input_uris=[
                    *self._base_input_uris(run),
                    cast(ArtifactPointer, run.artifacts.research_bundle).uri,
                ],
                dependencies=[research_call.work_unit_id],
            )
            architecture = cast(NarrativePlan, architecture)

            draft_call = self._calls.draft(
                execution_run_id=run.execution_run_id,
                project=project,
                architecture=architecture,
                research=research,
                evidence=evidence,
            )
            work_ids.append(draft_call.work_unit_id)
            draft, run = await self._ensure_draft(
                run,
                draft_call,
                round_index=0,
                persist=lambda value: self._production_artifacts.persist_draft(
                    run.execution_run_id, run.project_id, value, round_index=0
                ),
                input_uris=[
                    *self._base_input_uris(run),
                    cast(ArtifactPointer, run.artifacts.research_bundle).uri,
                    cast(ArtifactPointer, run.artifacts.narrative_plan).uri,
                ],
                dependencies=[architecture_call.work_unit_id],
            )
            if run.revision_rounds > 0:
                current_round = run.revision_rounds
                previous_draft = await self._production_artifacts.load(
                    run.artifacts.drafts[current_round - 1], ArticleDraftV2
                )
                previous_review = await self._production_artifacts.load(
                    run.artifacts.reviews[current_round - 1], EditorialReview
                )
                draft = await self._production_artifacts.load(
                    run.artifacts.drafts[current_round], ArticleDraftV2
                )
                draft_call = self._calls.edit(
                    execution_run_id=run.execution_run_id,
                    project=project,
                    draft=previous_draft,
                    review=previous_review,
                    research=research,
                    evidence=evidence,
                    round_index=current_round,
                )

            reviews: list[EditorialReview] = []
            while True:
                round_index = run.revision_rounds
                review_call = self._calls.critic(
                    execution_run_id=run.execution_run_id,
                    project=project,
                    draft=draft,
                    research=research,
                    evidence=evidence,
                    round_index=round_index,
                )
                work_ids.append(review_call.work_unit_id)
                review, run = await self._ensure_review(
                    run,
                    review_call,
                    round_index=round_index,
                    persist=lambda value, index=round_index: self._production_artifacts.persist_review(
                        run.execution_run_id, run.project_id, value, round_index=index
                    ),
                    input_uris=[*self._base_input_uris(run), run.artifacts.drafts[round_index].uri],
                    dependencies=[draft_call.work_unit_id],
                )
                reviews = await self._load_reviews(run)
                if review.ready_to_publish or round_index >= 2:
                    break
                edit_round = round_index + 1
                edit_call = self._calls.edit(
                    execution_run_id=run.execution_run_id,
                    project=project,
                    draft=draft,
                    review=review,
                    research=research,
                    evidence=evidence,
                    round_index=edit_round,
                )
                work_ids.append(edit_call.work_unit_id)
                draft, run = await self._ensure_draft(
                    run,
                    edit_call,
                    round_index=edit_round,
                    persist=lambda value, index=edit_round: self._production_artifacts.persist_draft(
                        run.execution_run_id, run.project_id, value, round_index=index
                    ),
                    input_uris=[
                        *self._base_input_uris(run),
                        run.artifacts.drafts[round_index].uri,
                        run.artifacts.reviews[round_index].uri,
                    ],
                    dependencies=[review_call.work_unit_id],
                )
                draft_call = edit_call

            run, record = await self._finalize(
                run,
                project_record,
                evidence,
                research,
                architecture,
                draft,
                reviews,
            )
        except Exception as error:
            await self._fail_current(run.execution_run_id, error, work_ids)
            raise
        return ProductionV2Result(execution_run=run, production_record=record)

    async def _context(self, execution_run_id: UUID):
        editorial = await self._editorial_runs.get(execution_run_id)
        if editorial is None or editorial.state is not EditorialV2State.PROJECT_APPROVED:
            raise ValueError("production requires a V2 Editorial Project approved at its gate")
        project_pointer = cast(ArtifactPointer, editorial.artifacts.approved_project)
        project_record = await self._editorial_artifacts.load(
            project_pointer, EditorialProjectRecordV2
        )
        digestion = await self._digestion_runs.get(execution_run_id)
        if digestion is None or digestion.state is not DigestionRunState.UNDERSTANDING_READY:
            raise ValueError("production requires a completed V2 digestion")
        matrix = await self._digestion_artifacts.load(
            cast(ArtifactPointer, digestion.artifacts.evidence_matrix), EvidenceMatrix
        )
        dossiers = [
            await self._digestion_artifacts.load(pointer, SourceDossier)
            for pointer in digestion.artifacts.source_dossiers
        ]
        run = await self._production_runs.get(execution_run_id)
        if run is None:
            run = ProductionExecutionRunV2(
                execution_run_id=execution_run_id,
                project_id=project_record.project_id,
                editorial_project_id=project_record.editorial_project.editorial_project_id,
                state=ProductionV2State.PROJECT_APPROVED,
                artifacts=ProductionArtifactRefsV2(approved_project=project_pointer),
            )
            await self._production_runs.create(run)
        else:
            if (
                run.project_id != project_record.project_id
                or run.editorial_project_id != project_record.editorial_project.editorial_project_id
                or run.artifacts.approved_project != project_pointer
            ):
                raise ValueError("production run conflicts with the approved Editorial Project")
            if run.state is ProductionV2State.FAILED:
                run = await self._production_runs.transition(
                    execution_run_id,
                    lambda current: _updated_run(
                        current,
                        state=cast(ProductionV2State, current.resume_state),
                        resume_state=None,
                        failed_work_unit_ids=[],
                        error_class=None,
                        error_message=None,
                    ),
                )
        return digestion, editorial, project_record, run, matrix, dossiers

    async def _ensure_evidence(self, run, project, matrix, dossiers):
        if run.artifacts.evidence_bundle is not None:
            return await self._production_artifacts.load(
                run.artifacts.evidence_bundle, ProductionEvidenceBundle
            ), run
        existing = await self._production_artifacts.find_evidence_bundle(
            run.execution_run_id, run.project_id
        )
        if existing is None:
            evidence = self._evidence_builder.build(
                execution_run_id=run.execution_run_id,
                project=project,
                matrix=matrix,
                dossiers=dossiers,
            )
            pointer = await self._production_artifacts.persist_evidence_bundle(
                run.execution_run_id, run.project_id, evidence
            )
        else:
            pointer, evidence = existing
        run = await self._production_runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=ProductionV2State.EVIDENCE_READY,
                artifacts=current.artifacts.model_copy(update={"evidence_bundle": pointer}),
            ),
        )
        return evidence, run

    async def _ensure_single_call(
        self,
        run,
        call,
        *,
        pointer_field,
        next_state,
        persist,
        input_uris,
        dependencies=None,
    ):
        pointer = getattr(run.artifacts, pointer_field)
        if pointer is not None:
            return await self._production_artifacts.load(pointer, call.output_schema), run
        value = await self._execute(call, run, persist, input_uris, dependencies or [])
        pointer = await self._pointer_for_work(run.execution_run_id, call.work_unit_id)
        run = await self._production_runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=next_state,
                completed_model_calls=current.completed_model_calls + 1,
                artifacts=current.artifacts.model_copy(update={pointer_field: pointer}),
            ),
        )
        return value, run

    async def _ensure_draft(
        self,
        run,
        call: PreparedProductionCall[ArticleDraftV2],
        *,
        round_index: int,
        persist,
        input_uris,
        dependencies,
    ):
        if len(run.artifacts.drafts) > round_index:
            return await self._production_artifacts.load(
                run.artifacts.drafts[round_index], ArticleDraftV2
            ), run
        if len(run.artifacts.drafts) != round_index:
            raise ValueError("draft rounds must be persisted contiguously")
        value = await self._execute(call, run, persist, input_uris, dependencies)
        pointer = await self._pointer_for_work(run.execution_run_id, call.work_unit_id)
        run = await self._production_runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=ProductionV2State.DRAFT_READY,
                revision_rounds=round_index,
                completed_model_calls=current.completed_model_calls + 1,
                artifacts=current.artifacts.model_copy(
                    update={"drafts": [*current.artifacts.drafts, pointer]}
                ),
            ),
        )
        return cast(ArticleDraftV2, value), run

    async def _ensure_review(
        self,
        run,
        call: PreparedProductionCall[EditorialReview],
        *,
        round_index: int,
        persist,
        input_uris,
        dependencies,
    ):
        if len(run.artifacts.reviews) > round_index:
            return await self._production_artifacts.load(
                run.artifacts.reviews[round_index], EditorialReview
            ), run
        if len(run.artifacts.reviews) != round_index:
            raise ValueError("review rounds must be persisted contiguously")
        value = await self._execute(call, run, persist, input_uris, dependencies)
        pointer = await self._pointer_for_work(run.execution_run_id, call.work_unit_id)
        run = await self._production_runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=ProductionV2State.REVIEWING,
                completed_model_calls=current.completed_model_calls + 1,
                artifacts=current.artifacts.model_copy(
                    update={"reviews": [*current.artifacts.reviews, pointer]}
                ),
            ),
        )
        return cast(EditorialReview, value), run

    async def _execute(self, call, run, persist, input_uris, dependencies):
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
                        dependencies=dependencies,
                    ),
                )
            ]
        )
        if outcomes[0].error is not None:
            raise outcomes[0].error
        return outcomes[0].value

    async def _finalize(
        self,
        run,
        project_record,
        evidence,
        research,
        architecture,
        draft,
        reviews,
    ):
        if not reviews:
            raise ValueError("final article requires at least one editorial review")
        existing_article = await self._production_artifacts.find_final_article(
            run.execution_run_id, run.project_id
        )
        if existing_article is None:
            article = FinalArticle(
                project_id=run.project_id,
                editorial_project_id=run.editorial_project_id,
                title=architecture.title,
                body_markdown=draft.body_markdown,
                word_count=len(draft.body_markdown.split()),
                quality_score=reviews[-1].quality_score,
                revision_rounds=run.revision_rounds,
            )
            article_pointer = await self._production_artifacts.persist_final_article(
                run.execution_run_id, run.project_id, article
            )
        else:
            article_pointer, article = existing_article
        existing_record = await self._production_artifacts.find_production_record(
            run.execution_run_id, run.project_id
        )
        if existing_record is None:
            record = ProductionRecordV2(
                execution_run_id=run.execution_run_id,
                project_id=run.project_id,
                editorial_project_id=run.editorial_project_id,
                evidence_bundle_sha256=evidence.content_sha256,
                research_bundle=research,
                narrative_plan=architecture,
                reviews=reviews,
                final_article=article,
                model=self._calls.config.model,
            )
            record_pointer = await self._production_artifacts.persist_production_record(
                run.execution_run_id, run.project_id, record
            )
        else:
            record_pointer, record = existing_record
        run = await self._production_runs.transition(
            run.execution_run_id,
            lambda current: _updated_run(
                current,
                state=ProductionV2State.ARTICLE_READY,
                artifacts=current.artifacts.model_copy(
                    update={
                        "final_article": article_pointer,
                        "production_record": record_pointer,
                    }
                ),
            ),
        )
        return run, record

    async def _load_reviews(self, run):
        return [
            await self._production_artifacts.load(pointer, EditorialReview)
            for pointer in run.artifacts.reviews
        ]

    async def _pointer_for_work(self, execution_run_id: UUID, work_unit_id: str):
        unit = await self._work.get(execution_run_id, work_unit_id)
        if unit is None or unit.status is not WorkUnitStatus.SUCCEEDED:
            raise ValueError(f"work unit has no persisted output: {work_unit_id}")
        return ArtifactPointer(uri=cast(str, unit.output_uri), sha256=cast(str, unit.output_sha256))

    async def _fail_current(self, execution_run_id, error, work_ids):
        current = await self._production_runs.get(execution_run_id)
        if current is None or current.state is ProductionV2State.FAILED:
            return
        failed_ids = []
        for work_unit_id in dict.fromkeys(work_ids):
            unit = await self._work.get(execution_run_id, work_unit_id)
            if unit is None or unit.status is not WorkUnitStatus.SUCCEEDED:
                failed_ids.append(work_unit_id)
        if not failed_ids:
            # Pre-call failures and failures after all paid outputs were checkpointed
            # remain at the last safe state; successful calls are never mislabeled.
            return
        await self._production_runs.transition(
            execution_run_id,
            lambda value: _updated_run(
                value,
                state=ProductionV2State.FAILED,
                resume_state=value.state,
                failed_work_unit_ids=failed_ids,
                error_class=type(error).__name__,
                error_message=str(error)[:4000],
            ),
        )

    @staticmethod
    def _base_input_uris(run):
        return [run.artifacts.approved_project.uri, cast(ArtifactPointer, run.artifacts.evidence_bundle).uri]


def _updated_run(run: ProductionExecutionRunV2, **updates) -> ProductionExecutionRunV2:
    return ProductionExecutionRunV2.model_validate(
        {**run.model_dump(), **updates, "updated_at": datetime.now(UTC)}
    )

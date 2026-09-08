from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Protocol, Sequence, TypeVar
from uuid import UUID

from pydantic import BaseModel

from .contracts import (
    CallUsage,
    EditorialProjectProposal,
    EditorialProjectRecord,
    EditorialUnderstanding,
    EditorialUnderstandingProposal,
    NarrativeAxisProposal,
    NarrativeAxisRecord,
    Paragraph,
    ParagraphBatchProposal,
    ParagraphCoordinate,
    ParagraphObservation,
    ParagraphNote,
    PlanningRequest,
    ProductRun,
    RunState,
)
from .gateway import ModelCallError, ModelGateway, ModelResult
from .prompts import SYSTEM, axis_prompt, paragraph_batch_prompt, project_prompt, understanding_prompt
from .segmentation import ParagraphBatch, make_batches, segment_sources


OutputT = TypeVar("OutputT", bound=BaseModel)


class Repository(Protocol):
    async def save_run(self, run: ProductRun) -> None: ...
    async def get_run(self, execution_run_id: UUID) -> ProductRun | None: ...
    async def save_structure(self, execution_run_id: UUID, paragraphs: Sequence[Paragraph]) -> None: ...
    async def save_note(self, execution_run_id: UUID, note: ParagraphNote) -> None: ...
    async def notes(self, execution_run_id: UUID) -> list[ParagraphNote]: ...
    async def record_usage(self, execution_run_id: UUID, project_id: UUID, usage: CallUsage) -> None: ...
    async def usage_report(self, execution_run_id: UUID) -> dict[str, object]: ...


class Hefesto:
    def __init__(
        self,
        repository: Repository,
        gateway: ModelGateway,
        *,
        batch_target_tokens: int = 12_000,
        concurrency: int = 6,
    ) -> None:
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        self._repository = repository
        self._gateway = gateway
        self._batch_target_tokens = batch_target_tokens
        self._concurrency = concurrency

    async def digest(
        self,
        request: PlanningRequest,
        *,
        execution_run_id: UUID | None = None,
    ) -> ProductRun:
        run = await self._load_or_create(request, execution_run_id)
        if run.understanding is not None:
            return run
        paragraphs = segment_sources(request.sources)
        await self._repository.save_structure(run.execution_run_id, paragraphs)
        existing = {item.paragraph_id: item for item in await self._repository.notes(run.execution_run_id)}
        missing = [item for item in paragraphs if item.paragraph_id not in existing]
        try:
            if missing:
                await self._run_batches(run, make_batches(missing, target_tokens=self._batch_target_tokens))
            notes = await self._repository.notes(run.execution_run_id)
            if [item.paragraph_id for item in notes] != [item.paragraph_id for item in paragraphs]:
                raise ValueError("digestion did not produce exactly one ordered note per paragraph")
            result = await self._call(
                run,
                task_type="editorial_understanding",
                prompt=understanding_prompt(request.brief, notes),
                schema=EditorialUnderstandingProposal,
                max_output_tokens=8_192,
                thinking_tokens=2_000,
            )
            self._validate_paragraphs(run, result.value.supporting_paragraphs)
            understanding = EditorialUnderstanding(
                **result.value.model_dump(),
                execution_run_id=run.execution_run_id,
                project_id=run.project_id,
            )
            run = run.model_copy(
                update={
                    "state": RunState.UNDERSTANDING_READY,
                    "understanding": understanding,
                    "error_class": None,
                    "error_message": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._repository.save_run(run)
            return run
        except Exception as error:
            await self._fail(run, error)
            raise

    async def resume_digest(self, execution_run_id: UUID) -> ProductRun:
        run = await self._repository.get_run(execution_run_id)
        if run is None:
            raise KeyError(f"unknown execution {execution_run_id}")
        return await self.digest(run.request, execution_run_id=execution_run_id)

    async def propose_axis(self, execution_run_id: UUID) -> ProductRun:
        run = await self._required_run(execution_run_id)
        if run.axis is not None:
            return run
        if run.understanding is None:
            raise ValueError("Narrative Axis requires completed editorial understanding")
        try:
            result = await self._call(
                run,
                task_type="narrative_axis",
                prompt=axis_prompt(run.request.brief, run.understanding),
                schema=NarrativeAxisProposal,
                max_output_tokens=2_048,
                thinking_tokens=1_000,
            )
            self._validate_paragraphs(run, result.value.supporting_paragraphs)
            axis = NarrativeAxisRecord(
                execution_run_id=run.execution_run_id,
                project_id=run.project_id,
                proposal=result.value,
            )
            run = run.model_copy(
                update={
                    "state": RunState.AXIS_PROPOSED, "axis": axis,
                    "error_class": None, "error_message": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._repository.save_run(run)
            return run
        except Exception as error:
            await self._fail(run, error)
            raise

    async def approve_axis_and_propose_project(self, execution_run_id: UUID) -> ProductRun:
        run = await self._required_run(execution_run_id)
        if run.editorial_project is not None:
            return run
        if run.axis is None or run.understanding is None:
            raise ValueError("Editorial Project requires a proposed Narrative Axis")
        if not run.axis.approved:
            approved_axis = run.axis.model_copy(
                update={"approved": True, "approved_at": datetime.now(UTC)}
            )
            run = run.model_copy(
                update={"state": RunState.AXIS_APPROVED, "axis": approved_axis, "updated_at": datetime.now(UTC)}
            )
            await self._repository.save_run(run)
        try:
            result = await self._call(
                run,
                task_type="editorial_project",
                prompt=project_prompt(run.request.brief, run.understanding, run.axis),
                schema=EditorialProjectProposal,
                max_output_tokens=6_144,
                thinking_tokens=2_000,
            )
            if result.value.duration_minutes != run.request.brief.duration_minutes:
                raise ValueError("Editorial Project changed the requested duration")
            for subject in result.value.source_supported_subjects:
                self._validate_paragraphs(run, subject.supporting_paragraphs)
            project = EditorialProjectRecord(
                execution_run_id=run.execution_run_id,
                project_id=run.project_id,
                narrative_axis_id=run.axis.narrative_axis_id,
                proposal=result.value,
            )
            run = run.model_copy(
                update={
                    "state": RunState.PROJECT_PROPOSED,
                    "editorial_project": project,
                    "error_class": None,
                    "error_message": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._repository.save_run(run)
            return run
        except Exception as error:
            await self._fail(run, error)
            raise

    async def receipt(self, execution_run_id: UUID) -> dict[str, object]:
        run = await self._required_run(execution_run_id)
        return {
            "status": run.state.value,
            "project_id": str(run.project_id),
            "execution_run_id": str(run.execution_run_id),
            "understanding": run.understanding.model_dump(mode="json") if run.understanding else None,
            "narrative_axis": run.axis.model_dump(mode="json") if run.axis else None,
            "editorial_project": run.editorial_project.model_dump(mode="json") if run.editorial_project else None,
            "error_class": run.error_class,
            "error_message": run.error_message,
            "usage": await self._repository.usage_report(execution_run_id),
        }

    async def _run_batches(self, run: ProductRun, batches: Sequence[ParagraphBatch]) -> None:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def execute(batch: ParagraphBatch) -> None:
            async with semaphore:
                result = await self._call(
                    run,
                    task_type="paragraph_batch",
                    prompt=paragraph_batch_prompt(run.request.brief, batch.paragraphs),
                    schema=ParagraphBatchProposal,
                    max_output_tokens=12_288,
                    thinking_tokens=1_000,
                )
                expected = list(range(1, len(batch.paragraphs) + 1))
                actual = [item.paragraph_number for item in result.value.notes]
                if actual != expected or len(actual) != len(set(actual)):
                    raise ValueError("batch must return exactly one ordered note per paragraph")
                for proposal in result.value.notes:
                    paragraph = batch.paragraphs[proposal.paragraph_number - 1]
                    sentence_by_ordinal = {item.ordinal: item for item in paragraph.sentences}
                    observations: list[ParagraphObservation] = []
                    for observation in proposal.observations:
                        ordinals = observation.evidence_sentence_ordinals
                        if len(ordinals) != len(set(ordinals)) or any(
                            item not in sentence_by_ordinal for item in ordinals
                        ):
                            raise ValueError(
                                "paragraph observation contains invalid sentence coordinates"
                            )
                        observations.append(
                            ParagraphObservation(
                                **observation.model_dump(),
                                evidence_sentence_ids=[
                                    sentence_by_ordinal[item].sentence_id for item in ordinals
                                ],
                            )
                        )
                    note = ParagraphNote(
                        paragraph_id=paragraph.paragraph_id,
                        observations=observations,
                        brief_relevance=proposal.brief_relevance,
                        source_id=paragraph.source_id,
                        source_ordinal=paragraph.source_ordinal,
                        paragraph_ordinal=paragraph.ordinal,
                    )
                    await self._repository.save_note(run.execution_run_id, note)

        results = await asyncio.gather(*(execute(batch) for batch in batches), return_exceptions=True)
        errors = [item for item in results if isinstance(item, BaseException)]
        if errors:
            raise errors[0]

    async def _call(
        self,
        run: ProductRun,
        *,
        task_type: str,
        prompt: str,
        schema: type[OutputT],
        max_output_tokens: int,
        thinking_tokens: int,
    ) -> ModelResult[OutputT]:
        try:
            result = await self._gateway.generate(
                task_type=task_type,
                prompt=prompt,
                schema=schema,
                system_instruction=SYSTEM,
                max_output_tokens=max_output_tokens,
                thinking_tokens=thinking_tokens,
            )
        except ModelCallError as error:
            if error.usage is not None:
                failed = error.usage.model_copy(update={"status": "failed"})
                await self._repository.record_usage(run.execution_run_id, run.project_id, failed)
            raise
        await self._repository.record_usage(run.execution_run_id, run.project_id, result.usage)
        return result

    async def _load_or_create(self, request: PlanningRequest, execution_run_id: UUID | None) -> ProductRun:
        if execution_run_id is not None:
            existing = await self._repository.get_run(execution_run_id)
            if existing is None:
                created = ProductRun(
                    execution_run_id=execution_run_id,
                    project_id=request.project_id,
                    request=request,
                )
                await self._repository.save_run(created)
                return created
            if existing.request != request:
                raise ValueError("resume request differs from persisted request")
            if existing.state is RunState.FAILED:
                existing = existing.model_copy(
                    update={
                        "state": RunState.DIGESTING,
                        "error_class": None,
                        "error_message": None,
                        "updated_at": datetime.now(UTC),
                    }
                )
                await self._repository.save_run(existing)
            return existing
        run = ProductRun(project_id=request.project_id, request=request)
        await self._repository.save_run(run)
        return run

    async def _required_run(self, execution_run_id: UUID) -> ProductRun:
        run = await self._repository.get_run(execution_run_id)
        if run is None:
            raise KeyError(f"unknown execution {execution_run_id}")
        return run

    def _validate_paragraphs(
        self, run: ProductRun, values: Sequence[ParagraphCoordinate]
    ) -> None:
        known = {
            (item.source_ordinal, item.ordinal)
            for item in segment_sources(run.request.sources)
        }
        supplied = {
            (item.source_ordinal, item.paragraph_ordinal)
            for item in values
        }
        if not values or len(supplied) != len(values) or not supplied.issubset(known):
            raise ValueError("editorial output referenced an unknown paragraph")

    async def _fail(self, run: ProductRun, error: Exception) -> None:
        failed = run.model_copy(
            update={
                "state": RunState.FAILED,
                "error_class": type(error).__name__,
                "error_message": str(error)[:4000],
                "updated_at": datetime.now(UTC),
            }
        )
        await self._repository.save_run(failed)

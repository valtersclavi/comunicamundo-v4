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
    ParagraphObservation,
    ParagraphNote,
    ParagraphNoteProposal,
    PlanningRequest,
    ProductRun,
    RunState,
)
from .gateway import ModelCallError, ModelGateway, ModelResult
from .prompts import SYSTEM, axis_prompt, paragraph_prompt, project_prompt, understanding_prompt
from .segmentation import segment_sources


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
        concurrency: int = 6,
    ) -> None:
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        self._repository = repository
        self._gateway = gateway
        self._concurrency = concurrency

    async def digest(
        self,
        request: PlanningRequest,
        *,
        execution_run_id: UUID | None = None,
    ) -> ProductRun:
        run = await self.prepare(request, execution_run_id=execution_run_id)
        return await self._digest_prepared(run)

    async def digest_prepared(self, execution_run_id: UUID) -> ProductRun:
        run = await self._required_run(execution_run_id)
        if run.state is RunState.FAILED:
            raise ValueError("failed digestion must be resumed explicitly")
        return await self._digest_prepared(run)

    async def _digest_prepared(self, run: ProductRun) -> ProductRun:
        if run.understanding is not None:
            return run
        if run.state is RunState.SOURCE_STRUCTURE_READY:
            run = run.model_copy(
                update={"state": RunState.DIGESTING, "updated_at": datetime.now(UTC)}
            )
            await self._repository.save_run(run)
        paragraphs = segment_sources(run.request.sources)
        existing = {item.paragraph_id: item for item in await self._repository.notes(run.execution_run_id)}
        missing = [item for item in paragraphs if item.paragraph_id not in existing]
        try:
            if missing:
                await self._run_paragraphs(run, missing)
            notes = await self._repository.notes(run.execution_run_id)
            if [item.paragraph_id for item in notes] != [item.paragraph_id for item in paragraphs]:
                raise ValueError("digestion did not produce exactly one ordered note per paragraph")
            result = await self._call(
                run,
                task_type="editorial_understanding",
                prompt=understanding_prompt(run.request.brief, notes),
                schema=EditorialUnderstandingProposal,
                max_output_tokens=8_192,
                thinking_tokens=2_000,
            )
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

    async def prepare(
        self,
        request: PlanningRequest,
        *,
        execution_run_id: UUID | None = None,
    ) -> ProductRun:
        run = await self._load_or_create(request, execution_run_id)
        paragraphs = segment_sources(request.sources)
        await self._repository.save_structure(run.execution_run_id, paragraphs)
        return run

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

    async def _run_paragraphs(
        self, run: ProductRun, paragraphs: Sequence[Paragraph]
    ) -> None:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def execute(paragraph: Paragraph) -> None:
            async with semaphore:
                result = await self._call(
                    run,
                    task_type="paragraph_digest",
                    prompt=paragraph_prompt(run.request.brief, paragraph),
                    schema=ParagraphNoteProposal,
                    max_output_tokens=32_768,
                    thinking_tokens=0,
                )
                proposal = result.value
                note = ParagraphNote(
                    paragraph_id=paragraph.paragraph_id,
                    observations=[
                        ParagraphObservation(**observation.model_dump())
                        for observation in proposal.observations
                    ],
                    brief_relevance=proposal.brief_relevance,
                    source_id=paragraph.source_id,
                    source_ordinal=paragraph.source_ordinal,
                    paragraph_ordinal=paragraph.ordinal,
                )
                await self._repository.save_note(run.execution_run_id, note)

        results = await asyncio.gather(
            *(execute(paragraph) for paragraph in paragraphs), return_exceptions=True
        )
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
        for attempt in range(6):
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
                    failed = error.usage.model_copy(
                        update={"status": "failed", "error_message": str(error)[:2000]}
                    )
                    await self._repository.record_usage(
                        run.execution_run_id, run.project_id, failed
                    )
                if error.retryable and attempt < 5:
                    await asyncio.sleep(min(30, 2 ** (attempt + 1)))
                    continue
                if not error.retryable and attempt == 0:
                    continue
                raise
            await self._repository.record_usage(
                run.execution_run_id, run.project_id, result.usage
            )
            return result
        raise AssertionError("unreachable")

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
                        "state": RunState.SOURCE_STRUCTURE_READY,
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

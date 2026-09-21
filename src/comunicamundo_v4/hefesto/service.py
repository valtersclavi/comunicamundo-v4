from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Sequence
from datetime import UTC, datetime
from hashlib import sha256
from math import ceil
import re
from time import perf_counter
from typing import Protocol, TypeVar
from uuid import UUID

from pydantic import BaseModel

from .batching import candidate_packets, passages_from_paragraphs, split_packet
from .contracts import (
    CallUsage,
    ContentReviewProposal,
    EditorialBrief,
    EditorialCondensation,
    EditorialCondensationProposal,
    EditorialCorpus,
    EditorialKnowledgeUnit,
    EditorialProjectProposal,
    EditorialProjectRecord,
    EditorialProjectScaffold,
    EditorialSubject,
    FinalTextProposal,
    FinalTextRecord,
    Paragraph,
    Passage,
    PlanningRequest,
    ProductRun,
    PreservationReviewProposal,
    RunState,
    SelectionBatch,
    SelectionFlags,
    SubjectEditorialCorpus,
)
from .gateway import ModelCallError, ModelGateway, ModelResult
from .prompts import (
    SYSTEM,
    condensation_prompt,
    content_draft_prompt,
    content_review_prompt,
    content_revision_prompt,
    preservation_review_prompt,
    product_style_prompt,
    project_scaffold_prompt,
    project_prompt,
    render_condensations,
    render_passages,
    selection_prompt,
    subject_corpus_prompt,
)
from .segmentation import segment_sources


OutputT = TypeVar("OutputT", bound=BaseModel)


class Repository(Protocol):
    async def save_run(self, run: ProductRun) -> None: ...
    async def get_run(self, execution_run_id: UUID) -> ProductRun | None: ...
    async def save_structure(
        self, execution_run_id: UUID, paragraphs: Sequence[Paragraph]
    ) -> None: ...
    async def save_selection_batch(
        self, execution_run_id: UUID, batch: SelectionBatch
    ) -> None: ...
    async def selection_batches(
        self, execution_run_id: UUID
    ) -> list[SelectionBatch]: ...
    async def save_condensation(
        self, execution_run_id: UUID, unit: EditorialCondensation
    ) -> None: ...
    async def condensations(
        self, execution_run_id: UUID
    ) -> list[EditorialCondensation]: ...
    async def work_report(self, execution_run_id: UUID) -> dict[str, object]: ...
    async def record_usage(
        self, execution_run_id: UUID, project_id: UUID, usage: CallUsage
    ) -> None: ...
    async def usage_report(self, execution_run_id: UUID) -> dict[str, object]: ...
    async def record_stages(
        self, execution_run_id: UUID, stages: Sequence[dict[str, object]]
    ) -> None: ...
    async def stage_report(
        self, execution_run_id: UUID
    ) -> list[dict[str, object]]: ...
    async def record_product_receipt(
        self, execution_run_id: UUID, payload: dict[str, object]
    ) -> None: ...


class Hefesto:
    def __init__(
        self,
        repository: Repository,
        gateway: ModelGateway,
        *,
        concurrency: int = 4,
        selection_input_tokens: int = 24_000,
        comprehension_input_tokens: int = 120_000,
        condensation_input_tokens: int = 60_000,
    ) -> None:
        if concurrency <= 0:
            raise ValueError("concurrency must be positive")
        if min(
            selection_input_tokens,
            comprehension_input_tokens,
            condensation_input_tokens,
        ) < 1_000:
            raise ValueError("token budgets must leave room for a complete semantic unit")
        self._repository = repository
        self._gateway = gateway
        self._concurrency = concurrency
        self._selection_budget = selection_input_tokens
        self._comprehension_budget = comprehension_input_tokens
        self._condensation_budget = condensation_input_tokens
        self._passage_target = min(8_000, selection_input_tokens // 3)

    async def prepare(
        self,
        request: PlanningRequest,
        *,
        execution_run_id: UUID | None = None,
    ) -> ProductRun:
        run = await self._load_or_create(request, execution_run_id)
        await self._repository.save_structure(
            run.execution_run_id, segment_sources(request.sources)
        )
        return run

    async def select(self, execution_run_id: UUID) -> ProductRun:
        run = await self._required_run(execution_run_id)
        if run.editorial_project is not None:
            return run
        run = await self._activate(run, RunState.SELECTING)
        paragraphs = segment_sources(run.request.sources)
        passages = passages_from_paragraphs(
            paragraphs, passage_token_target=self._passage_target
        )
        titles = {
            ordinal: source.title
            for ordinal, source in enumerate(run.request.sources, start=1)
        }
        try:
            specs = await self._selection_specs(run, passages, titles)
            existing = {
                item.batch_id: item
                for item in await self._repository.selection_batches(execution_run_id)
            }
            missing = [item for item in specs if item[0] not in existing]
            if missing:
                await self._run_selection_batches(run, missing)
            selected = await self._selected_passages(run, passages)
            if not selected:
                raise ValueError(
                    "the supplied sources contain no material that can support this brief"
                )
            run = run.model_copy(
                update={
                    "state": RunState.COMPREHENDING,
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

    async def propose_project(self, execution_run_id: UUID) -> ProductRun:
        run = await self._required_run(execution_run_id)
        if run.editorial_project is not None:
            return run
        if run.state is not RunState.COMPREHENDING:
            raise ValueError("Editorial Project requires completed source selection")
        paragraphs = segment_sources(run.request.sources)
        passages = passages_from_paragraphs(
            paragraphs, passage_token_target=self._passage_target
        )
        titles = {
            ordinal: source.title
            for ordinal, source in enumerate(run.request.sources, start=1)
        }
        try:
            selected = await self._selected_passages(run, passages)
            material = render_passages(selected, titles)
            direct_prompt = project_prompt(
                run.request.brief,
                material,
                material_kind="original selected source passages",
            )
            if await self._count_tokens(direct_prompt) <= self._comprehension_budget:
                return await self._create_project(
                    run,
                    material,
                    "original selected source passages",
                )

            readings = await self._condense_passages(run, selected, titles)
            level = 2
            while True:
                combined = render_condensations(readings)
                final_prompt = project_prompt(
                    run.request.brief,
                    combined,
                    material_kind="bounded editorial comprehensions of selected passages",
                )
                if await self._count_tokens(final_prompt) <= self._comprehension_budget:
                    return await self._create_project(
                        run,
                        combined,
                        "bounded editorial comprehensions of selected passages",
                    )
                readings = await self._condense_readings(run, readings, level)
                level += 1
        except Exception as error:
            await self._fail(run, error)
            raise

    async def resume_plan(self, execution_run_id: UUID) -> ProductRun:
        run = await self._required_run(execution_run_id)
        if run.editorial_project is not None:
            return run
        if run.state is RunState.FAILED:
            run = run.model_copy(
                update={
                    "state": RunState.SOURCE_STRUCTURE_READY,
                    "error_class": None,
                    "error_message": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._repository.save_run(run)
        await self.select(execution_run_id)
        return await self.propose_project(execution_run_id)

    async def approve_project(self, execution_run_id: UUID) -> ProductRun:
        run = await self._required_run(execution_run_id)
        if run.state is RunState.PROJECT_APPROVED:
            return run
        if run.state is not RunState.PROJECT_PROPOSED or run.editorial_project is None:
            raise ValueError("Editorial Project is not awaiting approval")
        project = run.editorial_project.model_copy(update={"approved": True})
        run = run.model_copy(
            update={
                "state": RunState.PROJECT_APPROVED,
                "editorial_project": project,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._repository.save_run(run)
        return run

    async def produce_text(self, execution_run_id: UUID) -> ProductRun:
        run = await self._required_run(execution_run_id)
        if run.final_text is not None:
            return run
        if run.editorial_project is None or not run.editorial_project.approved:
            raise ValueError("Final text requires an approved Editorial Project")
        if run.state is RunState.FAILED:
            run = run.model_copy(
                update={
                    "state": RunState.PROJECT_APPROVED,
                    "error_class": None,
                    "error_message": None,
                    "updated_at": datetime.now(UTC),
                }
            )
            await self._repository.save_run(run)
        if run.state not in {RunState.PROJECT_APPROVED, RunState.PRODUCING_TEXT}:
            raise ValueError("Final text cannot be produced from the current state")
        run = await self._activate(run, RunState.PRODUCING_TEXT)
        try:
            project = run.editorial_project.proposal
            if project.editorial_corpus is None:
                raise ValueError(
                    "Editorial Project has no canonical editorial corpus"
                )
            target_words = _target_words(run.request.brief)
            section_targets = _section_targets(project, target_words)

            if run.content_draft is None:
                draft = await self._generate_step(
                    run,
                    stage="content_draft",
                    task_type="content_draft",
                    prompt=content_draft_prompt(
                        run.request.brief,
                        project,
                        target_words=target_words,
                        section_targets=section_targets,
                    ),
                    schema=FinalTextProposal,
                    max_output_tokens=16_384,
                    thinking_tokens=4_000,
                )
                run = await self._save_artifact(run, content_draft=draft)

            if run.content_review is None:
                review = await self._generate_step(
                    run,
                    stage="content_review",
                    task_type="content_review",
                    prompt=content_review_prompt(
                        run.request.brief,
                        project,
                        run.content_draft,
                    ),
                    schema=ContentReviewProposal,
                    max_output_tokens=8_192,
                    thinking_tokens=4_000,
                )
                run = await self._save_artifact(run, content_review=review)

            if run.corrected_content is None and run.content_review.issues:
                corrected = await self._generate_step(
                    run,
                    stage="content_revision",
                    task_type="content_revision",
                    prompt=content_revision_prompt(
                        run.request.brief,
                        project,
                        run.content_draft,
                        run.content_review,
                        target_words=target_words,
                        section_targets=section_targets,
                    ),
                    schema=FinalTextProposal,
                    max_output_tokens=16_384,
                    thinking_tokens=4_000,
                )
                run = await self._save_artifact(run, corrected_content=corrected)
            elif run.corrected_content is None:
                await self._repository.record_stages(
                    run.execution_run_id,
                    [{
                        "stage": "content_revision",
                        "status": "skipped",
                        "duration_ms": 0,
                        "artifact": {"reason": "editor requested no corrections"},
                    }],
                )
                run = await self._save_artifact(
                    run, corrected_content=run.content_draft
                )


            if run.styled_text is None:
                styled = await self._generate_step(
                    run,
                    stage="product_styling",
                    task_type="product_styling",
                    prompt=product_style_prompt(
                        run.corrected_content,
                        product=run.request.brief.product,
                        language=run.request.brief.output_language,
                        audience=run.request.brief.audience,
                        target_words=target_words,
                    ),
                    schema=FinalTextProposal,
                    max_output_tokens=16_384,
                    thinking_tokens=3_000,
                )
                run = await self._save_artifact(run, styled_text=styled)

            run = await self._verify_and_repair_style(run)
            return await self._complete_final_text(run)
        except Exception as error:
            latest = await self._required_run(run.execution_run_id)
            await self._fail(latest, error)
            raise

    async def receipt(self, execution_run_id: UUID) -> dict[str, object]:
        run = await self._required_run(execution_run_id)
        return {
            "status": run.state.value,
            "project_id": str(run.project_id),
            "execution_run_id": str(run.execution_run_id),
            "provisional_editorial_project": (
                run.request.provisional_project.model_dump(mode="json")
                if run.request.provisional_project else None
            ),
            "supported_provisional_subject_indexes": (
                run.request.supported_provisional_subject_indexes
            ),
            "editorial_project": (
                run.editorial_project.model_dump(mode="json")
                if run.editorial_project else None
            ),
            "final_text": (
                run.final_text.model_dump(mode="json") if run.final_text else None
            ),
            "content_pipeline": {
                "target_words": _target_words(run.request.brief),
                "content_draft": (
                    run.content_draft.model_dump(mode="json")
                    if run.content_draft else None
                ),
                "content_review": (
                    run.content_review.model_dump(mode="json")
                    if run.content_review else None
                ),
                "corrected_content": (
                    run.corrected_content.model_dump(mode="json")
                    if run.corrected_content else None
                ),
                "styled_text": (
                    run.styled_text.model_dump(mode="json")
                    if run.styled_text else None
                ),
                "preservation_review": (
                    run.preservation_review.model_dump(mode="json")
                    if run.preservation_review else None
                ),
            },
            "processing": await self._repository.work_report(execution_run_id),
            "error_class": run.error_class,
            "error_message": run.error_message,
            "stages": await self._repository.stage_report(execution_run_id),
            "usage": await self._repository.usage_report(execution_run_id),
        }

    async def record_stages(
        self, execution_run_id: UUID, stages: Sequence[dict[str, object]]
    ) -> None:
        await self._repository.record_stages(execution_run_id, stages)

    async def record_product_receipt(
        self, execution_run_id: UUID, payload: dict[str, object]
    ) -> None:
        await self._repository.record_product_receipt(execution_run_id, payload)

    async def usage_report(self, execution_run_id: UUID) -> dict[str, object]:
        return await self._repository.usage_report(execution_run_id)

    async def _selection_specs(
        self,
        run: ProductRun,
        passages: Sequence[Passage],
        titles: dict[int, str | None],
    ) -> list[tuple[str, str, int, list[Passage]]]:
        by_source: dict[int, list[Passage]] = defaultdict(list)
        for passage in passages:
            by_source[passage.source_ordinal].append(passage)
        measured: list[tuple[str, str, int, list[Passage]]] = []
        for source_ordinal, source_passages in by_source.items():
            candidates = candidate_packets(
                source_passages,
                token_budget=self._selection_budget * 3 // 4,
                render=lambda items: selection_prompt(
                    run.request.brief,
                    items,
                    source_title=titles[source_ordinal],
                ),
            )
            for candidate in candidates:
                measured.extend(
                    await self._measure_selection_packet(
                        run, candidate, source_passages, titles[source_ordinal]
                    )
                )
        return measured

    async def _measure_selection_packet(
        self,
        run: ProductRun,
        core: list[Passage],
        source_passages: Sequence[Passage],
        source_title: str | None,
    ) -> list[tuple[str, str, int, list[Passage]]]:
        first_index = core[0].ordinal - 1
        last_index = core[-1].ordinal - 1
        previous = source_passages[first_index - 1] if first_index > 0 else None
        following = (
            source_passages[last_index + 1]
            if last_index + 1 < len(source_passages) else None
        )
        prompt = selection_prompt(
            run.request.brief,
            core,
            source_title=source_title,
            previous_context=previous,
            next_context=following,
        )
        input_tokens = await self._count_tokens(prompt)
        if input_tokens <= self._selection_budget:
            identity = "selection-v1:" + ":".join(item.passage_id for item in core)
            batch_id = sha256(identity.encode()).hexdigest()
            return [(batch_id, prompt, input_tokens, core)]
        left, right = split_packet(core)
        return [
            *await self._measure_selection_packet(
                run, left, source_passages, source_title
            ),
            *await self._measure_selection_packet(
                run, right, source_passages, source_title
            ),
        ]

    async def _run_selection_batches(
        self,
        run: ProductRun,
        specs: Sequence[tuple[str, str, int, list[Passage]]],
    ) -> None:
        semaphore = asyncio.Semaphore(self._concurrency)

        async def execute(spec: tuple[str, str, int, list[Passage]]) -> None:
            batch_id, prompt, input_tokens, passages = spec
            async with semaphore:
                result = await self._call(
                    run,
                    task_type="source_selection",
                    prompt=prompt,
                    schema=SelectionFlags,
                    max_output_tokens=2_048,
                    thinking_tokens=0,
                )
                allowed = {item.ordinal for item in passages}
                flags = _validated_flags(result.value, allowed)
                batch = SelectionBatch(
                    batch_id=batch_id,
                    source_id=passages[0].source_id,
                    source_ordinal=passages[0].source_ordinal,
                    first_passage_ordinal=passages[0].ordinal,
                    last_passage_ordinal=passages[-1].ordinal,
                    input_tokens=input_tokens,
                    flags=flags,
                )
                await self._repository.save_selection_batch(
                    run.execution_run_id, batch
                )

        results = await asyncio.gather(
            *(execute(spec) for spec in specs), return_exceptions=True
        )
        errors = [item for item in results if isinstance(item, BaseException)]
        if errors:
            raise errors[0]

    async def _selected_passages(
        self, run: ProductRun, passages: Sequence[Passage]
    ) -> list[Passage]:
        selected: dict[int, set[int]] = defaultdict(set)
        by_source: dict[int, dict[int, Passage]] = defaultdict(dict)
        for passage in passages:
            by_source[passage.source_ordinal][passage.ordinal] = passage
        for batch in await self._repository.selection_batches(run.execution_run_id):
            flags = batch.flags
            direct = {
                *flags.relevant,
                *flags.central,
                *flags.uncertain,
                *flags.include_previous,
                *flags.include_next,
            }
            selected[batch.source_ordinal].update(direct)
            selected[batch.source_ordinal].update(
                ordinal - 1 for ordinal in flags.include_previous
            )
            selected[batch.source_ordinal].update(
                ordinal + 1 for ordinal in flags.include_next
            )
        return [
            passage
            for passage in passages
            if passage.ordinal in selected[passage.source_ordinal]
            and passage.ordinal in by_source[passage.source_ordinal]
        ]

    async def _condense_passages(
        self,
        run: ProductRun,
        passages: Sequence[Passage],
        titles: dict[int, str | None],
    ) -> list[EditorialCondensation]:
        def prompt(items: Sequence[Passage]) -> str:
            return condensation_prompt(
                run.request.brief, render_passages(items, titles), level=1
            )

        candidates = candidate_packets(
            passages,
            token_budget=self._condensation_budget * 3 // 4,
            render=prompt,
        )
        packets: list[tuple[list[Passage], str, int]] = []
        for candidate in candidates:
            packets.extend(
                await self._measure_packet(candidate, prompt, self._condensation_budget)
            )
        existing = {
            item.unit_id: item
            for item in await self._repository.condensations(run.execution_run_id)
        }
        semaphore = asyncio.Semaphore(self._concurrency)

        async def execute(
            ordinal: int, items: list[Passage], text: str, tokens: int
        ) -> EditorialCondensation:
            identity = "condensation-v1:1:" + ":".join(
                item.passage_id for item in items
            )
            unit_id = sha256(identity.encode()).hexdigest()
            if unit_id in existing:
                return existing[unit_id]
            async with semaphore:
                result = await self._call(
                    run,
                    task_type="editorial_comprehension_part",
                    prompt=text,
                    schema=EditorialCondensationProposal,
                    max_output_tokens=4_096,
                    thinking_tokens=1_000,
                )
                unit = EditorialCondensation(
                    unit_id=unit_id,
                    level=1,
                    ordinal=ordinal,
                    source_ordinals=sorted({item.source_ordinal for item in items}),
                    input_tokens=tokens,
                    reading=result.value.reading,
                )
                await self._repository.save_condensation(
                    run.execution_run_id, unit
                )
                return unit

        return list(
            await asyncio.gather(
                *(
                    execute(ordinal, items, text, tokens)
                    for ordinal, (items, text, tokens) in enumerate(packets, start=1)
                )
            )
        )

    async def _condense_readings(
        self,
        run: ProductRun,
        readings: Sequence[EditorialCondensation],
        level: int,
    ) -> list[EditorialCondensation]:
        def prompt(items: Sequence[EditorialCondensation]) -> str:
            return condensation_prompt(
                run.request.brief, render_condensations(items), level=level
            )

        candidates = candidate_packets(
            readings,
            token_budget=self._condensation_budget * 3 // 4,
            render=prompt,
        )
        packets: list[tuple[list[EditorialCondensation], str, int]] = []
        for candidate in candidates:
            packets.extend(
                await self._measure_packet(candidate, prompt, self._condensation_budget)
            )
        if len(packets) >= len(readings):
            raise ValueError("editorial comprehension cannot be reduced within the token budget")
        existing = {
            item.unit_id: item
            for item in await self._repository.condensations(run.execution_run_id)
        }
        result: list[EditorialCondensation] = []
        for ordinal, (items, text, tokens) in enumerate(packets, start=1):
            identity = f"condensation-v1:{level}:" + ":".join(
                item.unit_id for item in items
            )
            unit_id = sha256(identity.encode()).hexdigest()
            if unit_id in existing:
                result.append(existing[unit_id])
                continue
            response = await self._call(
                run,
                task_type="editorial_comprehension_merge",
                prompt=text,
                schema=EditorialCondensationProposal,
                max_output_tokens=4_096,
                thinking_tokens=1_000,
            )
            unit = EditorialCondensation(
                unit_id=unit_id,
                level=level,
                ordinal=ordinal,
                source_ordinals=sorted(
                    {source for item in items for source in item.source_ordinals}
                ),
                input_tokens=tokens,
                reading=response.value.reading,
            )
            await self._repository.save_condensation(run.execution_run_id, unit)
            result.append(unit)
        return result

    async def _measure_packet(
        self,
        items: list[OutputT],
        render: object,
        budget: int,
    ) -> list[tuple[list[OutputT], str, int]]:
        prompt = render(items)  # type: ignore[operator]
        tokens = await self._count_tokens(prompt)
        if tokens <= budget:
            return [(items, prompt, tokens)]
        left, right = split_packet(items)
        return [
            *await self._measure_packet(left, render, budget),
            *await self._measure_packet(right, render, budget),
        ]

    async def _create_project(
        self,
        run: ProductRun,
        material: str,
        material_kind: str,
    ) -> ProductRun:
        scaffold_result = await self._call(
            run,
            task_type="editorial_project_scaffold",
            prompt=project_scaffold_prompt(
                run.request.brief,
                material,
                material_kind=material_kind,
                provisional_project=run.request.provisional_project,
                supported_subject_indexes=(
                    run.request.supported_provisional_subject_indexes
                ),
                existing_project=run.request.existing_editorial_project,
            ),
            schema=EditorialProjectScaffold,
            max_output_tokens=8_192,
            thinking_tokens=3_000,
        )
        scaffold, corpus_subjects = _anchor_scaffold(
            scaffold_result.value,
            run.request,
        )
        if (
            run.request.provisional_project is None
            and run.request.existing_editorial_project is None
            and scaffold.duration_minutes < run.request.brief.duration_minutes
        ):
            raise ValueError("Editorial Project shortened the requested duration")
        subjects = _normalize_subject_minutes(
            scaffold.source_supported_subjects,
            scaffold.duration_minutes,
        )
        corpus_titles = {item.title for item in corpus_subjects}
        corpus_subjects = [item for item in subjects if item.title in corpus_titles]
        existing_corpus = (
            run.request.existing_editorial_project.editorial_corpus
            if run.request.existing_editorial_project is not None
            else None
        )
        units: list[EditorialKnowledgeUnit] = list(
            existing_corpus.units if existing_corpus is not None else []
        )
        scaffold = scaffold.model_copy(update={"source_supported_subjects": subjects})
        for subject in corpus_subjects:
            corpus_result = await self._call(
                run,
                task_type="editorial_subject_corpus",
                prompt=subject_corpus_prompt(
                    run.request.brief,
                    scaffold,
                    subject,
                    material,
                    material_kind=material_kind,
                ),
                schema=SubjectEditorialCorpus,
                max_output_tokens=4_096,
                thinking_tokens=2_000,
            )
            units.extend(corpus_result.value.units)
        value = EditorialProjectProposal(
            working_title=scaffold.working_title,
            editorial_promise=scaffold.editorial_promise,
            narrative_axis=scaffold.narrative_axis,
            duration_minutes=scaffold.duration_minutes,
            source_supported_subjects=subjects,
            editorial_corpus=EditorialCorpus(
                governing_understanding=_combined_governing_understanding(
                    existing_corpus,
                    scaffold.corpus_governing_understanding,
                ),
                units=units,
                forbidden_inferences=_combined_forbidden_inferences(
                    existing_corpus,
                    scaffold.forbidden_inferences,
                ),
            ),
            model_knowledge_suggestions=[],
            source_enrichment_opportunities=[],
        )
        project = EditorialProjectRecord(
            execution_run_id=run.execution_run_id,
            project_id=run.project_id,
            proposal=value,
        )
        proposed = run.model_copy(
            update={
                "state": RunState.PROJECT_PROPOSED,
                "editorial_project": project,
                "error_class": None,
                "error_message": None,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._repository.save_run(proposed)
        return proposed

    async def _complete_final_text(self, run: ProductRun) -> ProductRun:
        if run.editorial_project is None:
            raise ValueError("Final text requires an Editorial Project")
        if run.styled_text is None or run.preservation_review is None:
            raise ValueError("Final text pipeline is incomplete")
        if not run.preservation_review.approved:
            raise ValueError("Styled text failed final preservation verification")
        record = FinalTextRecord(
            execution_run_id=run.execution_run_id,
            project_id=run.project_id,
            editorial_project_id=run.editorial_project.editorial_project_id,
            proposal=run.styled_text,
            word_count=_word_count(run.styled_text.body),
        )
        completed = run.model_copy(
            update={
                "state": RunState.TEXT_READY,
                "final_text": record,
                "error_class": None,
                "error_message": None,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._repository.save_run(completed)
        return completed

    async def _verify_and_repair_style(self, run: ProductRun) -> ProductRun:
        if run.corrected_content is None or run.styled_text is None:
            raise ValueError("Style verification requires both text versions")
        if run.preservation_review is None:
            review = await self._generate_step(
                run,
                stage="final_verification",
                task_type="final_verification",
                prompt=preservation_review_prompt(
                    run.corrected_content,
                    run.styled_text,
                    product=run.request.brief.product,
                ),
                schema=PreservationReviewProposal,
                max_output_tokens=8_192,
                thinking_tokens=3_000,
            )
            run = await self._save_artifact(run, preservation_review=review)
        if run.preservation_review.approved:
            return run

        correction = "; ".join(
            [run.preservation_review.summary, *run.preservation_review.differences]
        )
        repaired = await self._generate_step(
            run,
            stage="product_style_correction",
            task_type="product_style_correction",
            prompt=product_style_prompt(
                run.styled_text,
                product=run.request.brief.product,
                language=run.request.brief.output_language,
                audience=run.request.brief.audience,
                target_words=_target_words(run.request.brief),
                correction_request=correction,
                reference_text=run.corrected_content,
            ),
            schema=FinalTextProposal,
            max_output_tokens=16_384,
            thinking_tokens=3_000,
        )
        run = await self._save_artifact(
            run, styled_text=repaired, preservation_review=None
        )
        review = await self._generate_step(
            run,
            stage="final_reverification",
            task_type="final_reverification",
            prompt=preservation_review_prompt(
                run.corrected_content,
                repaired,
                product=run.request.brief.product,
            ),
            schema=PreservationReviewProposal,
            max_output_tokens=8_192,
            thinking_tokens=3_000,
        )
        run = await self._save_artifact(run, preservation_review=review)
        if not review.approved:
            raise ValueError("Styled text failed final preservation verification")
        return run

    async def _save_artifact(self, run: ProductRun, **updates: object) -> ProductRun:
        saved = run.model_copy(
            update={**updates, "updated_at": datetime.now(UTC)}
        )
        await self._repository.save_run(saved)
        return saved

    async def _generate_step(
        self,
        run: ProductRun,
        *,
        stage: str,
        task_type: str,
        prompt: str,
        schema: type[OutputT],
        max_output_tokens: int,
        thinking_tokens: int,
    ) -> OutputT:
        started = perf_counter()
        await self._repository.record_stages(
            run.execution_run_id,
            [{"stage": stage, "status": "running", "duration_ms": 0}],
        )
        try:
            result = await self._call(
                run,
                task_type=task_type,
                prompt=prompt,
                schema=schema,
                max_output_tokens=max_output_tokens,
                thinking_tokens=thinking_tokens,
            )
        except Exception as error:
            await self._repository.record_stages(
                run.execution_run_id,
                [{
                    "stage": stage,
                    "status": "failed",
                    "duration_ms": int((perf_counter() - started) * 1000),
                    "error_class": type(error).__name__,
                    "error_message": str(error)[:2000],
                }],
            )
            raise
        usage = result.usage
        await self._repository.record_stages(
            run.execution_run_id,
            [{
                "stage": stage,
                "status": "completed",
                "duration_ms": int((perf_counter() - started) * 1000),
                "model_invocations": 1,
                "prompt_tokens": usage.prompt_tokens,
                "cached_tokens": usage.cached_tokens,
                "output_tokens": usage.output_tokens,
                "reasoning_tokens": usage.reasoning_tokens,
                "total_tokens": usage.total_tokens,
                "provider_latency_ms": usage.latency_ms,
                "rated_cost_usd": usage.rated_cost_usd or "0",
                "artifact": {"schema": schema.__name__},
            }],
        )
        return result.value

    async def _count_tokens(self, prompt: str) -> int:
        for attempt in range(3):
            try:
                return await self._gateway.count_tokens(prompt)
            except ModelCallError:
                if attempt == 2:
                    raise
                await asyncio.sleep(2 ** attempt)
        raise AssertionError("unreachable")

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
        for attempt in range(3):
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
                if error.retryable and attempt < 2:
                    await asyncio.sleep(2 ** (attempt + 1))
                    continue
                raise
            await self._repository.record_usage(
                run.execution_run_id, run.project_id, result.usage
            )
            return result
        raise AssertionError("unreachable")

    async def _load_or_create(
        self, request: PlanningRequest, execution_run_id: UUID | None
    ) -> ProductRun:
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
            return existing
        run = ProductRun(project_id=request.project_id, request=request)
        await self._repository.save_run(run)
        return run

    async def _required_run(self, execution_run_id: UUID) -> ProductRun:
        run = await self._repository.get_run(execution_run_id)
        if run is None:
            raise KeyError(f"unknown execution {execution_run_id}")
        return run

    async def _activate(self, run: ProductRun, state: RunState) -> ProductRun:
        if run.state is state:
            return run
        active = run.model_copy(
            update={
                "state": state,
                "error_class": None,
                "error_message": None,
                "updated_at": datetime.now(UTC),
            }
        )
        await self._repository.save_run(active)
        return active

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


def _anchor_scaffold(
    scaffold: EditorialProjectScaffold,
    request: PlanningRequest,
) -> tuple[EditorialProjectScaffold, list[EditorialSubject]]:
    provisional = request.provisional_project
    if provisional is not None:
        supported = request.supported_provisional_subject_indexes
        if not supported:
            raise ValueError("no provisional subject was supported by acquired sources")
        anchored_subjects: list[EditorialSubject] = []
        for position, index in enumerate(supported):
            original = provisional.subjects[index]
            generated = (
                scaffold.source_supported_subjects[position]
                if position < len(scaffold.source_supported_subjects)
                else None
            )
            anchored_subjects.append(EditorialSubject(
                title=original.title,
                editorial_role=_preserve_and_enrich(
                    original.editorial_role,
                    generated.editorial_role if generated else "",
                ),
                depth=_preserve_and_enrich(
                    original.desired_depth,
                    generated.depth if generated else "",
                ),
                allocated_minutes=original.allocated_minutes,
            ))
        duration = (
            provisional.duration_minutes
            if len(supported) == len(provisional.subjects)
            else max(1, ceil(sum(item.allocated_minutes for item in anchored_subjects)))
        )
        anchored = scaffold.model_copy(update={
            "working_title": provisional.working_title,
            "editorial_promise": provisional.editorial_promise,
            "narrative_axis": provisional.narrative_axis,
            "duration_minutes": duration,
            "source_supported_subjects": anchored_subjects,
            "model_knowledge_suggestions": [],
            "source_enrichment_opportunities": [],
        })
        return anchored, anchored_subjects

    existing = request.existing_editorial_project
    if existing is not None:
        known_titles = {_subject_key(item.title) for item in existing.source_supported_subjects}
        additions = [
            item
            for item in scaffold.source_supported_subjects
            if _subject_key(item.title) not in known_titles
        ]
        subjects = [*existing.source_supported_subjects, *additions]
        duration = max(
            existing.duration_minutes,
            ceil(
                sum(item.allocated_minutes for item in existing.source_supported_subjects)
                + sum(item.allocated_minutes for item in additions)
            ),
        )
        anchored = scaffold.model_copy(update={
            "working_title": existing.working_title,
            "editorial_promise": existing.editorial_promise,
            "narrative_axis": existing.narrative_axis,
            "duration_minutes": duration,
            "source_supported_subjects": subjects,
            "model_knowledge_suggestions": [],
            "source_enrichment_opportunities": [],
        })
        return anchored, additions

    clean = scaffold.model_copy(update={
        "model_knowledge_suggestions": [],
        "source_enrichment_opportunities": [],
    })
    return clean, list(clean.source_supported_subjects)


def _subject_key(value: str) -> str:
    return re.sub(r"\W+", " ", value.casefold()).strip()


def _preserve_and_enrich(original: str, enrichment: str) -> str:
    original_key = _subject_key(original)
    enrichment_key = _subject_key(enrichment)
    if not enrichment_key or enrichment_key == original_key:
        return original
    if original_key in enrichment_key:
        return enrichment
    if enrichment_key in original_key:
        return original
    return f"{original} {enrichment}".strip()


def _combined_governing_understanding(
    existing: EditorialCorpus | None,
    new_value: str,
) -> str:
    if existing is None:
        return new_value
    if new_value in existing.governing_understanding:
        return existing.governing_understanding
    return f"{existing.governing_understanding} {new_value}"[:1_400].strip()


def _combined_forbidden_inferences(
    existing: EditorialCorpus | None,
    new_values: Sequence[str],
) -> list[str]:
    values = [*(existing.forbidden_inferences if existing else []), *new_values]
    return list(dict.fromkeys(values))[:6]


def _normalize_subject_minutes(
    subjects: Sequence[EditorialSubject],
    duration_minutes: int,
) -> list[EditorialSubject]:
    total = sum(item.allocated_minutes for item in subjects)
    if not subjects:
        return []
    if total <= 0:
        share = round(duration_minutes / len(subjects), 2)
        values = [share for _ in subjects]
    else:
        values = [
            round(duration_minutes * item.allocated_minutes / total, 2)
            for item in subjects
        ]
    drift = round(duration_minutes - sum(values), 2)
    values[-1] = round(values[-1] + drift, 2)
    return [
        subject.model_copy(update={"allocated_minutes": max(value, 0.01)})
        for subject, value in zip(subjects, values, strict=True)
    ]


def _validated_flags(flags: SelectionFlags, allowed: set[int]) -> SelectionFlags:
    central = set(flags.central) & allowed
    relevant = (set(flags.relevant) & allowed) - central
    uncertain = (set(flags.uncertain) & allowed) - central - relevant
    return SelectionFlags(
        relevant=sorted(relevant),
        central=sorted(central),
        uncertain=sorted(uncertain),
        include_previous=sorted(set(flags.include_previous) & allowed),
        include_next=sorted(set(flags.include_next) & allowed),
    )


def _word_count(text: str) -> int:
    return len(re.findall(r"\b[\wÀ-ÖØ-öø-ÿ]+(?:['’\-][\wÀ-ÖØ-öø-ÿ]+)*\b", text))


def _target_words(brief: EditorialBrief) -> int:
    product = brief.product.strip().lower()
    rates = {
        "article": 140,
        "blog": 140,
        "newsletter": 140,
        "report": 130,
        "video": 135,
        "voice-over": 135,
        "voiceover": 135,
        "podcast": 145,
        "presentation": 120,
    }
    words_per_minute = rates.get(product, 140)
    return brief.duration_minutes * words_per_minute


def _section_targets(
    project: EditorialProjectProposal, target_words: int
) -> list[dict[str, object]]:
    subjects = project.source_supported_subjects
    targets: list[int] = []
    assigned = 0
    for index, subject in enumerate(subjects):
        if index == len(subjects) - 1:
            words = target_words - assigned
        else:
            words = round(
                target_words * subject.allocated_minutes / project.duration_minutes
            )
            assigned += words
        targets.append(words)
    return [
        {
            "title": subject.title,
            "allocated_minutes": subject.allocated_minutes,
            "target_words": words,
        }
        for subject, words in zip(subjects, targets, strict=True)
    ]

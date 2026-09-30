from __future__ import annotations

from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.batching import passages_from_paragraphs
from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProjectProposal,
    NormalizedSource,
    PlanningRequest,
    RunState,
)
from comunicamundo_v4.hefesto.prompts import selection_prompt
from comunicamundo_v4.hefesto.segmentation import segment_sources
from comunicamundo_v4.hefesto.service import Hefesto
from fakes import FakeGateway, MemoryRepository


def request(*, long: bool = False) -> PlanningRequest:
    filler = (
        " Esta frase desenvolve o argumento com contexto e qualificação." * 15
        if long else ""
    )
    return PlanningRequest(
        project_id=uuid4(),
        brief=EditorialBrief(
            theme="The user's precise intention",
            approach="Read the sources as editorial material",
            duration_minutes=8,
            output_language="pt-BR",
            audience="A defined audience",
            product="article",
        ),
        sources=[
            NormalizedSource(
                title="One",
                language="en",
                text="\n\n".join(
                    [
                        "First claim. Its qualification." + filler,
                        "A contrasting example appears here." + filler,
                        "A third contribution." + filler,
                    ]
                    * (8 if long else 1)
                ),
            ),
            NormalizedSource(
                title="Two",
                language="en",
                text="\n\n".join(
                    [
                        "Another source contributes a different view." + filler,
                        "Its final paragraph adds context." + filler,
                    ]
                    * (8 if long else 1)
                ),
            ),
        ],
    )


def test_paragraphs_are_addresses_and_selection_outputs_only_flags() -> None:
    source = request()
    paragraphs = segment_sources(source.sources)
    passages = passages_from_paragraphs(paragraphs, passage_token_target=1_000)
    prompt = selection_prompt(
        source.brief, passages[:3], source_title=source.sources[0].title
    )
    assert len(paragraphs) == 5
    assert len(paragraphs[0].sentences) == 2
    assert "routing, not\nsummarization" in prompt
    assert "relevant" in prompt and "central" in prompt and "uncertain" in prompt
    assert paragraphs[0].paragraph_id not in prompt


def test_oversized_paragraph_is_divided_only_at_sentence_boundaries() -> None:
    text = ("Primeira sentença longa " * 12).strip() + ". " + (
        "Segunda sentença longa " * 12
    ).strip() + "."
    source = request()
    paragraph = segment_sources([
        NormalizedSource(title="Long", language="pt-BR", text=text)
    ])[0]
    passages = passages_from_paragraphs([paragraph], passage_token_target=100)
    assert len(passages) == 2
    assert passages[0].text.endswith(".")
    assert passages[1].text.endswith(".")
    assert "Primeira" in passages[0].text
    assert "Segunda" in passages[1].text


def test_provider_schema_uses_supported_inclusive_minimums() -> None:
    serialized = str(EditorialProjectProposal.model_json_schema())
    assert "exclusiveMinimum" not in serialized
    assert "'minimum': 1" in serialized


@pytest.mark.asyncio
async def test_prepare_persists_relational_structure_without_model_work() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway()
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(request())
    assert run.state is RunState.SOURCE_STRUCTURE_READY
    assert gateway.calls == []
    assert len(repository.structures[run.execution_run_id]) == 5


@pytest.mark.asyncio
async def test_short_corpus_goes_from_flags_directly_to_one_complete_project() -> None:
    source = request()
    repository = MemoryRepository()
    gateway = FakeGateway()
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(source)
    run = await hefesto.select(run.execution_run_id)
    run = await hefesto.propose_project(run.execution_run_id)

    task_types = [item["task_type"] for item in gateway.calls]
    assert task_types.count("source_selection") == 2
    assert task_types.count("editorial_project_scaffold") == 1
    assert task_types.count("editorial_subject_routing") == 1
    assert task_types.count("editorial_subject_reading") == 1
    assert not any("digest" in value for value in task_types)
    assert not any(value.startswith("editorial_comprehension") for value in task_types)
    assert run.state is RunState.PROJECT_PROPOSED
    assert run.editorial_project is not None
    assert run.editorial_project.proposal.narrative_axis.statement
    assert not run.editorial_project.approved
    assert all("The user's precise intention" in item["prompt"] for item in gateway.calls)


@pytest.mark.asyncio
async def test_long_corpus_uses_measured_bounded_comprehension_packets() -> None:
    source = request(long=True)
    repository = MemoryRepository()
    gateway = FakeGateway()
    hefesto = Hefesto(
        repository,
        gateway,
        selection_input_tokens=2_000,
        comprehension_input_tokens=1_000,
        condensation_input_tokens=1_000,
    )
    run = await hefesto.prepare(source)
    await hefesto.select(run.execution_run_id)
    completed = await hefesto.propose_project(run.execution_run_id)

    task_types = [item["task_type"] for item in gateway.calls]
    assert task_types.count("source_selection") < len(
        passages_from_paragraphs(
            segment_sources(source.sources), passage_token_target=666
        )
    )
    assert not any("comprehension_part" in task or "digest" in task for task in task_types)
    assert task_types.index("editorial_project_scaffold") < task_types.index("editorial_subject_routing")
    assert task_types.index("editorial_subject_routing") < task_types.index("editorial_subject_reading")
    assert completed.state is RunState.PROJECT_PROPOSED
    assert not await repository.condensations(run.execution_run_id)
    assert repository.readings
    assert all(item.prompt_tokens <= 5_000 for item in repository.usage[run.execution_run_id]
               if item.task_type == "editorial_subject_reading")



@pytest.mark.asyncio
async def test_multiple_sources_reach_the_same_final_comprehension() -> None:
    source = request()
    repository = MemoryRepository()
    gateway = FakeGateway()
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(source)
    await hefesto.select(run.execution_run_id)
    await hefesto.propose_project(run.execution_run_id)
    final_prompt = next(
        item["prompt"] for item in gateway.calls if item["task_type"] == "editorial_project_scaffold"
    )
    assert "First claim." not in final_prompt
    assert "Another source contributes" not in final_prompt
    reading = next(item["prompt"] for item in gateway.calls if item["task_type"] == "editorial_subject_reading")
    assert '"source_title": "One"' in reading
    assert '"source_title": "Two"' in reading


@pytest.mark.asyncio
async def test_resume_reuses_completed_flags_after_project_failure() -> None:
    source = request()
    repository = MemoryRepository()
    gateway = FakeGateway(fail_task="editorial_project_scaffold", fail_times=1)
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(source)
    await hefesto.select(run.execution_run_id)
    selection_calls = len(
        [item for item in gateway.calls if item["task_type"] == "source_selection"]
    )
    with pytest.raises(Exception, match="synthetic editorial_project_scaffold failure"):
        await hefesto.propose_project(run.execution_run_id)
    resumed = await hefesto.resume_plan(run.execution_run_id)
    assert resumed.state is RunState.PROJECT_PROPOSED
    assert len(
        [item for item in gateway.calls if item["task_type"] == "source_selection"]
    ) == selection_calls


@pytest.mark.asyncio
async def test_project_and_axis_share_one_human_gate() -> None:
    repository = MemoryRepository()
    hefesto = Hefesto(repository, FakeGateway())
    run = await hefesto.prepare(request())
    await hefesto.select(run.execution_run_id)
    proposed = await hefesto.propose_project(run.execution_run_id)
    assert proposed.editorial_project is not None
    assert proposed.editorial_project.proposal.narrative_axis.statement
    assert proposed.editorial_project.proposal.editorial_corpus is not None
    assert proposed.state is RunState.PROJECT_PROPOSED
    approved = await hefesto.approve_project(run.execution_run_id)
    assert approved.state is RunState.PROJECT_APPROVED
    assert approved.editorial_project is not None
    assert approved.editorial_project.approved


@pytest.mark.asyncio
async def test_approved_project_produces_persisted_editorial_pipeline() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway()
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(request())
    await hefesto.select(run.execution_run_id)
    await hefesto.propose_project(run.execution_run_id)
    await hefesto.approve_project(run.execution_run_id)

    completed = await hefesto.produce_text(run.execution_run_id)
    call_count = len(gateway.calls)
    repeated = await hefesto.produce_text(run.execution_run_id)

    assert completed.state is RunState.TEXT_READY
    assert completed.final_text is not None
    assert completed.final_text.proposal.title == "Narratively edited title"
    assert completed.final_text.proposal == completed.narrative_text
    assert completed.final_text.proposal != completed.styled_text
    assert gateway.calls[-1]["task_type"] == "final_narrative_edit"
    assert completed.final_text.word_count == 1_120
    assert repeated.final_text == completed.final_text
    assert completed.narrative_architecture is not None
    restored = type(completed).model_validate_json(completed.model_dump_json())
    assert restored.narrative_architecture == completed.narrative_architecture
    narrative_calls = [call for call in gateway.calls if call["task_type"] == "narrative_architecture"]
    assert len(narrative_calls) == 1
    text_calls = [
        item for item in gateway.calls
        if item["task_type"] in {
            "content_draft", "content_review",
            "product_styling", "final_verification",
        }
    ]
    assert [item["task_type"] for item in text_calls] == [
        "content_draft", "content_review", "product_styling",
        "final_verification",
    ]
    draft_prompt = str(text_calls[0]["prompt"])
    assert "APPROVED EDITORIAL STATE" in draft_prompt
    assert '"editorial_corpus"' in draft_prompt
    assert "SOURCE 1: One" not in draft_prompt
    assert "SOURCE 2: Two" not in draft_prompt
    assert "First claim. Its qualification." in draft_prompt
    assert "Another source contributes a different view." in draft_prompt
    style_prompt = str(text_calls[2]["prompt"])
    assert "EDITORIALLY CLOSED TEXT" in style_prompt
    assert "editorial_corpus" not in style_prompt
    assert "A concise intention of message" not in style_prompt
    assert "SOURCE 1" not in style_prompt
    for call in text_calls:
        assert completed.narrative_architecture.governing_question in str(call["prompt"])
    assert len(gateway.calls) == call_count


@pytest.mark.asyncio
async def test_final_text_requires_approval_and_resumes_after_model_failure() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway(fail_task="content_review", fail_times=1)
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(request())
    await hefesto.select(run.execution_run_id)
    await hefesto.propose_project(run.execution_run_id)

    with pytest.raises(ValueError, match="approved Editorial Project"):
        await hefesto.produce_text(run.execution_run_id)
    await hefesto.approve_project(run.execution_run_id)
    with pytest.raises(Exception, match="synthetic content_review failure"):
        await hefesto.produce_text(run.execution_run_id)
    assert repository.runs[run.execution_run_id].state is RunState.FAILED
    assert repository.runs[run.execution_run_id].content_draft is not None

    completed = await hefesto.produce_text(run.execution_run_id)
    assert completed.state is RunState.TEXT_READY
    assert completed.editorial_project is not None
    assert completed.editorial_project.approved
    assert len([call for call in gateway.calls if call["task_type"] == "narrative_architecture"]) == 1
    assert len([
        item for item in gateway.calls if item["task_type"] == "content_draft"
    ]) == 1


@pytest.mark.asyncio
async def test_duration_is_guidance_not_a_hard_acceptance_gate() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway(word_counts={"content_draft": 500})
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(request())
    await hefesto.select(run.execution_run_id)
    await hefesto.propose_project(run.execution_run_id)
    await hefesto.approve_project(run.execution_run_id)

    completed = await hefesto.produce_text(run.execution_run_id)

    assert completed.final_text is not None
    assert completed.final_text.word_count == 1_120
    tasks = [item["task_type"] for item in gateway.calls]
    assert "content_contract_correction" not in tasks


@pytest.mark.asyncio
async def test_rejected_style_is_corrected_and_reverified() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway(reject_verification_times=1)
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(request())
    await hefesto.select(run.execution_run_id)
    await hefesto.propose_project(run.execution_run_id)
    await hefesto.approve_project(run.execution_run_id)

    completed = await hefesto.produce_text(run.execution_run_id)

    assert completed.state is RunState.TEXT_READY
    assert completed.preservation_review is not None
    assert completed.preservation_review.approved
    tasks = [item["task_type"] for item in gateway.calls]
    assert "product_style_correction" in tasks
    assert "final_reverification" in tasks
    correction = next(
        str(item["prompt"])
        for item in gateway.calls
        if item["task_type"] == "product_style_correction"
    )
    assert "make only the local" in correction
    assert "EDITORIALLY CLOSED REFERENCE" in correction
    assert "CURRENT STYLED TEXT" in correction
    assert "APPROVED EDITORIAL STATE" not in correction
    assert completed.narrative_architecture.governing_question in correction
    reverification = next(call for call in gateway.calls if call["task_type"] == "final_reverification")
    assert completed.narrative_architecture.governing_question in str(reverification["prompt"])


@pytest.mark.asyncio
async def test_second_rejection_is_preserved_and_resume_continues_from_it() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway(reject_verification_times=2)
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(request())
    await hefesto.select(run.execution_run_id)
    await hefesto.propose_project(run.execution_run_id)
    await hefesto.approve_project(run.execution_run_id)

    with pytest.raises(
        ValueError, match="Styled text failed final preservation verification"
    ):
        await hefesto.produce_text(run.execution_run_id)

    failed = repository.runs[run.execution_run_id]
    assert failed.state is RunState.FAILED
    assert failed.preservation_review is not None
    assert not failed.preservation_review.approved
    repaired_text = failed.styled_text

    completed = await hefesto.produce_text(run.execution_run_id)

    assert completed.state is RunState.TEXT_READY
    assert completed.styled_text is not repaired_text
    tasks = [item["task_type"] for item in gateway.calls]
    assert tasks.count("final_verification") == 1
    assert tasks.count("product_style_correction") == 2
    assert tasks.count("final_reverification") == 2


@pytest.mark.asyncio
async def test_final_narrative_failure_resumes_without_repeating_validated_content() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway(fail_task="final_narrative_edit", fail_times=1)
    hefesto = Hefesto(repository, gateway)
    run = await hefesto.prepare(request())
    await hefesto.select(run.execution_run_id)
    await hefesto.propose_project(run.execution_run_id)
    await hefesto.approve_project(run.execution_run_id)

    with pytest.raises(Exception, match="synthetic final_narrative_edit failure"):
        await hefesto.produce_text(run.execution_run_id)
    failed = repository.runs[run.execution_run_id]
    assert failed.styled_text is not None
    assert failed.preservation_review.approved
    assert failed.narrative_text is None
    assert failed.final_text is None
    before = [call["task_type"] for call in gateway.calls]

    completed = await hefesto.produce_text(run.execution_run_id)
    assert [call["task_type"] for call in gateway.calls[len(before):]] == ["final_narrative_edit"]
    assert completed.final_text.proposal == completed.narrative_text
    assert completed.final_text.proposal != completed.styled_text
    saved = type(completed).model_validate_json(completed.model_dump_json())
    assert saved.narrative_text == completed.narrative_text
    receipt = await hefesto.receipt(run.execution_run_id)
    assert receipt["content_pipeline"]["narrative_text"] == completed.narrative_text.model_dump(mode="json")
    prompt = str(gateway.calls[-1]["prompt"])
    assert completed.styled_text.body in prompt
    assert "VALIDATED COMPLETE TEXT" in prompt
    assert "editorial_corpus" not in prompt
    assert "BINDING NARRATIVE ARCHITECTURE" not in prompt

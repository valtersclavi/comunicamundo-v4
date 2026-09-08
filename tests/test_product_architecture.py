from __future__ import annotations

from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import EditorialBrief, NormalizedSource, PlanningRequest, RunState
from comunicamundo_v4.hefesto.segmentation import make_batches, segment_sources
from comunicamundo_v4.hefesto.prompts import paragraph_batch_prompt
from comunicamundo_v4.hefesto.service import Hefesto
from fakes import FakeGateway, MemoryRepository


def request() -> PlanningRequest:
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
                text="First claim. Its qualification.\n\nA contrasting example appears here.\n\nA third contribution.",
            ),
            NormalizedSource(
                title="Two",
                language="en",
                text="Another source contributes a different view.\n\nIts final paragraph adds context.",
            ),
        ],
    )


def test_paragraph_is_comprehension_unit_and_sentence_is_evidence_address() -> None:
    paragraphs = segment_sources(request().sources)
    assert len(paragraphs) == 5
    assert [item.ordinal for item in paragraphs[:3]] == [1, 2, 3]
    assert len(paragraphs[0].sentences) == 2
    assert paragraphs[0].sentences[0].text == "First claim."
    batches = make_batches(paragraphs, target_tokens=1)
    assert [item.paragraph_id for batch in batches for item in batch.paragraphs] == [
        item.paragraph_id for item in paragraphs
    ]
    prompt = paragraph_batch_prompt(request().brief, paragraphs)
    assert all(item.paragraph_id not in prompt for item in paragraphs)
    assert '"paragraph_number": 1' in prompt


@pytest.mark.asyncio
async def test_brief_and_duration_guide_every_editorial_stage() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway()
    hefesto = Hefesto(repository, gateway, batch_target_tokens=12)
    run = await hefesto.digest(request())
    run = await hefesto.propose_axis(run.execution_run_id)
    run = await hefesto.approve_axis_and_propose_project(run.execution_run_id)
    assert run.state is RunState.PROJECT_PROPOSED
    for call in gateway.calls:
        assert "The user's precise intention" in call["prompt"]
        assert "8" in call["prompt"]
    assert [item["task_type"] for item in gateway.calls].count("editorial_understanding") == 1
    assert [item["task_type"] for item in gateway.calls].count("narrative_axis") == 1
    assert [item["task_type"] for item in gateway.calls].count("editorial_project") == 1
    assert run.axis is not None and run.axis.approved
    assert run.editorial_project is not None
    assert run.editorial_project.proposal.duration_minutes == 8
    assert sum(
        item.allocated_minutes for item in run.editorial_project.proposal.source_supported_subjects
    ) == 8
    assert run.editorial_project.proposal.model_knowledge_suggestions
    assert run.editorial_project.proposal.source_enrichment_opportunities


@pytest.mark.asyncio
async def test_parallel_batches_create_one_note_per_paragraph_without_ontology_coercion() -> None:
    source = request()
    repository = MemoryRepository()
    gateway = FakeGateway()
    run = await Hefesto(repository, gateway, batch_target_tokens=10, concurrency=4).digest(source)
    notes = await repository.notes(run.execution_run_id)
    assert len(notes) == len(segment_sources(source.sources))
    assert len({item.paragraph_id for item in notes}) == len(notes)
    assert all(
        item.observations[0].tags == ["free-tag-not-in-an-ontology"] for item in notes
    )
    assert all(len(item.observations) == 2 for item in notes)
    assert len([item for item in gateway.calls if item["task_type"] == "paragraph_batch"]) > 1


@pytest.mark.asyncio
async def test_batch_size_changes_calls_not_semantic_cardinality() -> None:
    source = request()
    small_repo, large_repo = MemoryRepository(), MemoryRepository()
    small_gateway, large_gateway = FakeGateway(), FakeGateway()
    small = await Hefesto(small_repo, small_gateway, batch_target_tokens=10).digest(source)
    large = await Hefesto(large_repo, large_gateway, batch_target_tokens=100_000).digest(source)
    assert len(await small_repo.notes(small.execution_run_id)) == len(await large_repo.notes(large.execution_run_id))
    assert len([c for c in small_gateway.calls if c["task_type"] == "paragraph_batch"]) > len(
        [c for c in large_gateway.calls if c["task_type"] == "paragraph_batch"]
    )


@pytest.mark.asyncio
async def test_resume_does_not_reread_completed_paragraphs() -> None:
    source = request()
    repository = MemoryRepository()
    gateway = FakeGateway(fail_understanding_once=True)
    hefesto = Hefesto(repository, gateway, batch_target_tokens=100_000)
    with pytest.raises(Exception, match="synthetic synthesis failure"):
        await hefesto.digest(source)
    execution_id = next(iter(repository.runs))
    paragraph_calls_before = len([item for item in gateway.calls if item["task_type"] == "paragraph_batch"])
    resumed = await hefesto.resume_digest(execution_id)
    paragraph_calls_after = len([item for item in gateway.calls if item["task_type"] == "paragraph_batch"])
    assert resumed.state is RunState.UNDERSTANDING_READY
    assert paragraph_calls_after == paragraph_calls_before


@pytest.mark.asyncio
async def test_axis_is_a_real_human_gate() -> None:
    repository = MemoryRepository()
    gateway = FakeGateway()
    hefesto = Hefesto(repository, gateway)
    digested = await hefesto.digest(request())
    proposed = await hefesto.propose_axis(digested.execution_run_id)
    assert proposed.state is RunState.AXIS_PROPOSED
    assert proposed.axis is not None and not proposed.axis.approved
    assert proposed.editorial_project is None

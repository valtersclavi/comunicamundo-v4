import pytest

from comunicamundo_v4.hefesto.contracts import (
    CallUsage, EditorialProjectRevisionHeader, EditorialRevisionPlan,
    EditorialRevisionSubject, EditorialSubject,
)
from comunicamundo_v4.hefesto.gateway import ModelResult
from comunicamundo_v4.hefesto.service import Hefesto
from fakes import FakeGateway, MemoryRepository
from test_product_architecture import request


class RevisionGateway(FakeGateway):
    plan = None

    async def generate(self, **kwargs):
        if kwargs['task_type'] == 'editorial_project_revision_plan':
            self.calls.append({'task_type': kwargs['task_type'], 'prompt': kwargs['prompt']})
            return ModelResult(value=self.plan, usage=CallUsage(
                task_type=kwargs['task_type'], prompt_tokens=10, output_tokens=5,
                reasoning_tokens=0, total_tokens=15, latency_ms=1, status='succeeded',
            ))
        return await super().generate(**kwargs)


@pytest.mark.parametrize('revisit', [False, True])
async def test_user_revision_can_change_structure_and_optionally_revisit_sources(revisit):
    repo, gateway = MemoryRepository(), RevisionGateway()
    service = Hefesto(repo, gateway)
    run = await service.prepare(request())
    await service.select(run.execution_run_id)
    run = await service.propose_project(run.execution_run_id)
    previous = run.editorial_project.proposal
    subject = EditorialSubject(title='User requested different angle', editorial_role='User direction',
        depth='Examine a different relationship', allocated_minutes=12)
    gateway.plan = EditorialRevisionPlan(
        header=EditorialProjectRevisionHeader(working_title='Revised by user',
            editorial_promise='A different user-authorized promise', narrative_axis=previous.narrative_axis),
        corpus_governing_understanding='The revised direction requested by the user.',
        duration_minutes=12,
        subjects=[EditorialRevisionSubject(subject=subject, revisit_sources=revisit)] if revisit else [
            EditorialRevisionSubject(subject=subject.model_copy(update={"allocated_minutes": 6})),
            EditorialRevisionSubject(subject=subject.model_copy(update={"title": "Second authorized angle", "allocated_minutes": 6})),
        ],
        retained_corpus_indexes=[] if revisit else [0],
    )
    gateway.calls.clear()
    if not revisit:
        async def forbidden_read(*args):
            raise AssertionError('A revision without rereading must not load source paragraphs')
        repo.structure = forbidden_read
    result = await service.revise_editorial_project(run.execution_run_id,
        'Replace the old subject with this different angle; revisit the sources if needed.')
    revised = result.editorial_project.proposal
    assert revised.working_title == 'Revised by user'
    assert revised.source_supported_subjects == [item.subject for item in gateway.plan.subjects]
    assert revised.duration_minutes == 12
    assert not result.editorial_project.approved
    assert result.final_text is None
    assert any(c['task_type'] == 'editorial_subject_routing' for c in gateway.calls) == revisit
    assert any(c['task_type'] == 'editorial_subject_digest' for c in gateway.calls) == revisit
    if not revisit:
        assert revised.editorial_corpus.units == previous.editorial_corpus.units
    else:
        prompt = next(c['prompt'] for c in gateway.calls if c['task_type'] == 'editorial_subject_routing')
        assert 'USER-AUTHORIZED REVISION' in prompt
        assert 'User requested different angle' in prompt

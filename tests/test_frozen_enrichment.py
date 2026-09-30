from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import (
    CallUsage, NormalizedSource, SourceEnrichmentAssessment,
)
from comunicamundo_v4.hefesto.gateway import ModelResult
from comunicamundo_v4.hefesto.segmentation import segment_sources
from comunicamundo_v4.hefesto.service import Hefesto, _target_words
from fakes import FakeGateway, MemoryRepository
from test_product_architecture import request


class EnrichmentGateway(FakeGateway):
    contributes = True
    target = None

    async def generate(self, **kwargs):
        if kwargs['task_type'] == 'source_enrichment_assessment':
            self.calls.append({"task_type": kwargs["task_type"], "prompt": kwargs["prompt"]})
            return ModelResult(value=SourceEnrichmentAssessment(
                contributes=self.contributes, reason='Only the requested new evidence.',
                target_subject_index=self.target, additional_minutes=3,
            ), usage=CallUsage(task_type=kwargs["task_type"], prompt_tokens=10, output_tokens=5,
                               total_tokens=15, reasoning_tokens=0, latency_ms=1, status="succeeded"))
        return await super().generate(**kwargs)


async def prepared():
    repo = MemoryRepository()
    gateway = EnrichmentGateway()
    service = Hefesto(repo, gateway)
    run = await service.prepare(request())
    await service.select(run.execution_run_id)
    run = await service.propose_project(run.execution_run_id)
    frozen = run.editorial_project.proposal.model_copy(deep=True)
    source = NormalizedSource(source_id=uuid4(), language='en', title='New source',
                              text='NEW_ONLY evidence. It qualifies the requested contribution.')
    new_rows = [p.model_copy(update={'source_ordinal': 3}) for p in segment_sources([source])]
    repo.structures[run.execution_run_id].extend(new_rows)
    run = run.model_copy(update={'request': run.request.model_copy(update={
        'sources': [*run.request.sources, source], 'existing_editorial_project': frozen,
    })})
    await repo.save_run(run)
    gateway.calls.clear()
    return repo, gateway, service, run, frozen, source


@pytest.mark.parametrize('target', [None, 0])
async def test_enrichment_preserves_frozen_content_and_reads_only_new_source(target):
    repo, gateway, service, run, frozen, source = await prepared()
    gateway.target = target
    updated, _ = await service.enrich_project(run.execution_run_id,
        opportunity=frozen.source_enrichment_opportunities[0], new_source_ids=[source.source_id])
    proposal = updated.editorial_project.proposal
    for field in ['working_title', 'editorial_promise', 'narrative_axis']:
        assert getattr(proposal, field) == getattr(frozen, field)
    assert proposal.editorial_corpus.units[:-1] == frozen.editorial_corpus.units
    assert proposal.editorial_corpus.forbidden_inferences == frozen.editorial_corpus.forbidden_inferences
    assert proposal.editorial_corpus.governing_understanding == frozen.editorial_corpus.governing_understanding
    for before, after in zip(frozen.source_supported_subjects, proposal.source_supported_subjects):
        assert before.model_dump(exclude={'allocated_minutes'}) == after.model_dump(exclude={'allocated_minutes'})
    assert proposal.duration_minutes == frozen.duration_minutes + 3
    assert _target_words(run.request.brief, proposal) == proposal.duration_minutes * 140
    assert not any(c['task_type'] in {'editorial_project_scaffold', 'editorial_subject_routing'} for c in gateway.calls)
    for c in gateway.calls:
        if c['task_type'] in {'source_selection', 'editorial_subject_reading'}:
            assert 'NEW_ONLY' in c['prompt']
            assert 'First claim.' not in c['prompt']
            assert 'EXTRACTION SCOPE' in c['prompt']
    await service.approve_project(run.execution_run_id)
    await service.produce_text(run.execution_run_id)
    draft = next(c for c in gateway.calls if c['task_type'] == 'content_draft')
    assert f'"duration_minutes": {proposal.duration_minutes}' in draft['prompt']
    assert str(proposal.duration_minutes * 140) in draft['prompt']


async def test_no_contribution_leaves_project_exactly_unchanged():
    repo, gateway, service, run, frozen, source = await prepared()
    gateway.contributes = False
    updated, _ = await service.enrich_project(run.execution_run_id,
        opportunity=frozen.source_enrichment_opportunities[0], new_source_ids=[source.source_id])
    assert updated.editorial_project == run.editorial_project
    assert updated.editorial_round == run.editorial_round


async def test_frozen_project_cannot_enter_general_replanning():
    repo, gateway, service, run, frozen, source = await prepared()
    await repo.save_run(run.model_copy(update={'editorial_project': None}))
    with pytest.raises(ValueError, match='scoped enrichment'):
        await service.propose_project(run.execution_run_id)
    assert gateway.calls == []

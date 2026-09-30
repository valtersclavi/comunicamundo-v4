from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.batching import passages_from_paragraphs
from comunicamundo_v4.hefesto.contracts import EditorialSubject, NormalizedSource
from comunicamundo_v4.hefesto.gateway import ModelResult
from comunicamundo_v4.hefesto.segmentation import segment_sources
from comunicamundo_v4.hefesto.service import Hefesto
from fakes import FakeGateway, MemoryRepository
from test_product_architecture import request


class EvidenceGateway(FakeGateway):
    invalid_index = False
    invented_quote = False

    async def generate(self, **kwargs):
        result = await super().generate(**kwargs)
        if kwargs['task_type'] == 'editorial_subject_reading':
            evidence = result.value.evidence[0]
            if self.invalid_index:
                evidence = evidence.model_copy(update={'passage_indexes': [9999]})
            if self.invented_quote:
                evidence = evidence.model_copy(update={'verbatim_excerpt': 'An invented quotation.'})
            return ModelResult(value=result.value.model_copy(update={'evidence': [evidence]}), usage=result.usage)
        return result


@pytest.mark.parametrize('invalid_index,invented_quote,message', [
    (True, False, 'outside its original packet'),
    (False, True, 'does not occur'),
])
async def test_invalid_grounding_is_not_saved(invalid_index, invented_quote, message):
    repo, gateway = MemoryRepository(), EvidenceGateway()
    gateway.invalid_index, gateway.invented_quote = invalid_index, invented_quote
    service = Hefesto(repo, gateway)
    run = await service.prepare(request())
    await service.select(run.execution_run_id)
    with pytest.raises(ValueError, match=message):
        await service.propose_project(run.execution_run_id)
    assert not repo.readings
    assert repo.runs[run.execution_run_id].editorial_project is None


async def test_new_source_local_index_resolves_to_its_real_identity_and_roundtrips():
    repo, gateway = MemoryRepository(), FakeGateway()
    service = Hefesto(repo, gateway)
    run = await service.prepare(request())
    source = NormalizedSource(source_id=uuid4(), title='Acquired source', language='en',
                              text='The newly acquired original supports this contribution.')
    rows = [p.model_copy(update={'source_ordinal': 7}) for p in segment_sources([source])]
    repo.structures[run.execution_run_id].extend(rows)
    passages = passages_from_paragraphs(rows, passage_token_target=1000)
    subject = EditorialSubject(title='Authorized opportunity', editorial_role='Add a perspective',
                               depth='Develop that perspective', allocated_minutes=2)
    unit = await service._create_subject_corpus(run, subject, passages, {7: source.title})
    restored = type(unit).model_validate_json(unit.model_dump_json())
    evidence = restored.evidence[0]
    assert evidence.source_ordinals == [7]
    assert evidence.passage_references[0].source_id == source.source_id
    assert evidence.passage_references[0].paragraph_id == rows[0].paragraph_id
    assert evidence.passage_references[0].passage_id == passages[0].passage_id
    calls = len(gateway.calls)
    assert await service._create_subject_corpus(run, subject, passages, {7: source.title}) == unit
    assert len(gateway.calls) == calls
    changed = subject.model_copy(update={'depth': 'Explicitly changed scope'})
    await service._create_subject_corpus(run, changed, passages, {7: source.title})
    assert len(gateway.calls) == calls + 1


async def test_missing_original_blocks_writing_instead_of_falling_back_to_notes():
    repo, gateway = MemoryRepository(), FakeGateway()
    service = Hefesto(repo, gateway)
    run = await service.prepare(request())
    await service.select(run.execution_run_id)
    proposed = await service.propose_project(run.execution_run_id)
    await service.approve_project(run.execution_run_id)
    ref = proposed.editorial_project.proposal.editorial_corpus.units[0].evidence[0].passage_references[0]
    repo.structures[run.execution_run_id] = [p for p in repo.structures[run.execution_run_id]
                                           if p.paragraph_id != ref.paragraph_id]
    gateway.calls.clear()
    with pytest.raises(ValueError, match='original is missing'):
        await service.produce_text(run.execution_run_id)
    assert not gateway.calls


async def test_packet_readings_preserve_originals_without_recompression():
    repo, gateway = MemoryRepository(), FakeGateway()
    service = Hefesto(repo, gateway, subject_input_tokens=2000)
    run = await service.prepare(request(long=True))
    rows = await repo.structure(run.execution_run_id)
    passages = passages_from_paragraphs(rows, passage_token_target=500)
    subject = EditorialSubject(title='Brief question', editorial_role='Answer it',
                               depth='Include qualifications', allocated_minutes=8)
    unit = await service._create_subject_corpus(run, subject, passages, {1:'One', 2:'Two'})
    assert len(gateway.calls) > 1
    assert {c['task_type'] for c in gateway.calls} == {'editorial_subject_reading'}
    references = {ref.passage_id for e in unit.evidence for ref in e.passage_references}
    assert references == {p.passage_id for p in passages}
    assert not await repo.condensations(run.execution_run_id)


async def test_selection_flags_cannot_leak_across_briefing_scopes():
    repo, gateway = MemoryRepository(), FakeGateway()
    service = Hefesto(repo, gateway)
    run = await service.prepare(request())
    await service.select(run.execution_run_id)
    passages = passages_from_paragraphs(await repo.structure(run.execution_run_id),
                                       passage_token_target=service._passage_target)
    assert await service._selected_passages(run, passages)
    changed = run.model_copy(update={'request': run.request.model_copy(update={
        'brief': run.request.brief.model_copy(update={'approach': 'A different authorized question'})
    })})
    assert not await service._selected_passages(changed, passages)

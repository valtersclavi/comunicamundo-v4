from hashlib import sha256
from types import SimpleNamespace
from uuid import NAMESPACE_URL, uuid5

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto import worker
from comunicamundo_v4.hefesto.contracts import (
    AcquiredSource, NormalizedSource, SourceCandidateJudgment,
    SourceEnrichmentAssessment, SourceSearchPlan, SubjectSearchQuery,
)
from comunicamundo_v4.hefesto.gateway import ModelResult
from comunicamundo_v4.hefesto.source_pipeline import AcquisitionPayload
from fakes import MemoryRepository
from test_frozen_enrichment import EnrichmentGateway, prepared
from test_source_pipeline import candidate, usage


class FlowRepository(MemoryRepository):
    async def save_structure(self, execution_id, paragraphs, assets=(), asset_links=()):
        known = {p.paragraph_id: p for p in self.structures.get(execution_id, [])}
        known.update({p.paragraph_id: p for p in paragraphs})
        self.structures[execution_id] = list(known.values())

    async def rollback_enrichment_sources(self, execution_id, source_ids, previous_round):
        self.structures[execution_id] = [p for p in self.structures[execution_id]
                                        if p.source_id not in source_ids]
        batches = self.saved_selections.get(execution_id, {})
        self.saved_selections[execution_id] = {k: v for k, v in batches.items()
                                               if v.source_id not in source_ids}

    async def usage_report(self, execution_id):
        report = await super().usage_report(execution_id)
        return {**report, 'calls': [u.model_dump(mode='json') for u in self.usage.get(execution_id, [])]}

    async def close(self):
        pass


class FlowGateway(EnrichmentGateway):
    target = 0

    async def generate(self, **kwargs):
        if kwargs['task_type'] == 'source_enrichment_assessment':
            self.contributes = 'NO_PROMISE' not in kwargs['prompt']
        result = await super().generate(**kwargs)
        if kwargs['task_type'] == 'editorial_subject_reading':
            marker = 'NO_PROMISE' if 'NO_PROMISE' in kwargs['prompt'] else 'PROMISED_EXPERIENCES'
            result = ModelResult(value=result.value.model_copy(update={
                'synthesis': marker, 'essential_points': [marker],
            }), usage=result.usage)
        return result


@pytest.mark.parametrize('successful', [True, False])
async def test_worker_replaces_unfulfilled_source_without_changing_promise(monkeypatch, successful):
    memory, _, service, run, frozen, unused_source = await prepared()
    # Begin with the original two sources, before this test's acquired source.
    original = run.model_copy(update={'request': run.request.model_copy(update={
        'sources': run.request.sources[:-1], 'existing_editorial_project': None,
    })})
    memory.structures[run.execution_run_id] = [p for p in memory.structures[run.execution_run_id]
                                              if p.source_id != unused_source.source_id]
    await memory.save_run(original)
    repo = FlowRepository()
    repo.__dict__.update(memory.__dict__)
    old_rows = list(repo.structures[run.execution_run_id])
    gateway = FlowGateway()
    from comunicamundo_v4.hefesto.service import Hefesto
    service = Hefesto(repo, gateway)
    opportunity = frozen.source_enrichment_opportunities[0]
    seen = []
    discovered = []

    class Editor:
        def __init__(self, *args, **kwargs):
            pass

        async def search_plan(self, brief, project):
            seen.append(brief)
            assert opportunity in brief.approach
            assert brief.approach.startswith('MANDATORY BRIEF ADDENDUM')
            return ModelResult(value=SourceSearchPlan(queries=[
                SubjectSearchQuery(subject_index=0, query=f'round {i}', provider='you')
                for i in range(4)
            ], editorial_coverage=[opportunity]), usage=usage('source_search_plan'))

        async def judge_candidate(self, brief, project, source, **kwargs):
            seen.append(brief)
            return ModelResult(value=SourceCandidateJudgment(
                decision='acquire', covered_subject_indexes=[0],
            ), usage=usage('source_candidate_judgment'))

    class Discovery:
        def __init__(self, *args, **kwargs):
            pass

        def discover_query(self, query, provider):
            discovered.append(query)
            return [candidate(candidate_id=sha256(query.encode()).hexdigest(),
                              title=query, url=f'https://example.org/{query[-1]}')]

    class Acquirer:
        def __init__(self, **kwargs):
            pass

        def acquire(self, source):
            text = 'PROMISED_EXPERIENCES' if successful and source.title == 'round 1' else 'NO_PROMISE'
            return AcquisitionPayload(record=AcquiredSource(candidate=source,
                status='acquired', normalized_text=text,
                normalized_text_sha256=sha256(text.encode()).hexdigest(),
                final_url=source.url, selected_route='you_contents'))

    def ingest(acquisition_request, payloads, **kwargs):
        seen.append(acquisition_request.brief)
        record = payloads[0].record
        return SimpleNamespace(request=SimpleNamespace(sources=[NormalizedSource(
            source_id=uuid5(NAMESPACE_URL, record.candidate.candidate_id),
            title=record.candidate.title, language='en', text=record.normalized_text,
        )]))

    async def services():
        return repo, service

    async def persist(hefesto, client, execution_id, stages, **kwargs):
        await hefesto.record_stages(execution_id, stages)

    async def restore(*args):
        pass

    monkeypatch.setattr(worker, 'services', services)
    monkeypatch.setattr(worker, 'storage_client', lambda: object())
    monkeypatch.setattr(worker, 'required', lambda name: 'test-value')
    monkeypatch.setattr(worker, 'VertexGateway', lambda **kwargs: gateway)
    monkeypatch.setattr(worker, 'EditorialSourceEditor', Editor)
    monkeypatch.setattr(worker, 'SourceDiscovery', Discovery)
    monkeypatch.setattr(worker, 'SourceAcquirer', Acquirer)
    monkeypatch.setattr(worker, 'YouSearchProvider', lambda *args: object())
    monkeypatch.setattr(worker, 'OpenAlexSearchProvider', lambda *args: object())
    monkeypatch.setattr(worker, 'ingest_acquired', ingest)
    monkeypatch.setattr(worker, '_persist_product_receipt', persist)
    monkeypatch.setattr(worker, '_restore_editable_receipt', restore)

    if successful:
        await worker.enrich_project_source(run.execution_run_id, 0)
    else:
        with pytest.raises(ValueError, match='No source fulfilled'):
            await worker.enrich_project_source(run.execution_run_id, 0)
    updated = repo.runs[run.execution_run_id]
    stage = next(s for s in repo.stages[run.execution_run_id] if s['stage'] == 'source_enrichment')
    assert all(brief.approach.startswith('MANDATORY BRIEF ADDENDUM') for brief in seen)
    assert all(opportunity in brief.approach for brief in seen)
    assert updated.request.brief == original.request.brief
    assert all(p in repo.structures[run.execution_run_id] for p in old_rows)
    assert 'NO_PROMISE' not in ''.join(p.text for p in repo.structures[run.execution_run_id])
    if successful:
        assert discovered == ['round 0', 'round 1']
        assert stage['status'] == 'completed' and stage['incorporated']
        assert [s['editorial_status'] for s in stage['sources']] == ['rejected', 'incorporated']
        assert updated.editorial_project.proposal.duration_minutes == frozen.duration_minutes + 3
        assert 'PROMISED_EXPERIENCES' in updated.editorial_project.proposal.source_supported_subjects[0].depth
        assert updated.editorial_project.proposal.editorial_corpus.units[:-1] == frozen.editorial_corpus.units
        assert len(updated.request.sources) == len(original.request.sources) + 1
    else:
        assert len(discovered) == 4
        assert updated == original
        assert stage['status'] == 'failed' and not stage['incorporated']
        assert all(s['editorial_status'] == 'rejected' for s in stage['sources'])
        assert updated.editorial_project.proposal.source_enrichment_opportunities == frozen.source_enrichment_opportunities
    recorded = [u.task_type for u in repo.usage[run.execution_run_id]]
    assert recorded.count('source_candidate_judgment') == len(discovered)


def test_successful_external_contribution_cannot_have_zero_duration():
    with pytest.raises(ValidationError, match='positive additional duration'):
        SourceEnrichmentAssessment(contributes=True, reason='Adds substantive arguments',
                                   target_subject_index=0, additional_minutes=0)

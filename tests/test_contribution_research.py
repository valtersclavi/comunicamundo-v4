from copy import deepcopy
from hashlib import sha256
import pytest

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief, ProvisionalEditorialProject, ProvisionalEditorialSubject,
    NarrativeAxisProposal, SourceSearchPlan, SubjectSearchQuery,
    SourceCandidateJudgment, SourcePromiseHint, AcquiredSource,
)
from comunicamundo_v4.hefesto.source_pipeline import AcquisitionPayload
from comunicamundo_v4.hefesto.contribution_research import ContributionResearch
from comunicamundo_v4.hefesto.gateway import ModelResult
from test_source_pipeline import candidate, usage


def context():
    brief = EditorialBrief(theme='Theme', approach='MANDATORY BRIEF ADDENDUM: lived experiences',
        duration_minutes=10, output_language='pt-BR', audience='Families', product='article')
    project = ProvisionalEditorialProject(working_title='Project', editorial_promise='Promise',
        narrative_axis=NarrativeAxisProposal(statement='Axis', rationale='Reason'),
        duration_minutes=2, subjects=[ProvisionalEditorialSubject(title='Experiences',
        editorial_role='Perspective', desired_depth='Accounts', allocated_minutes=2)],
        disclaimer='Sources required')
    return brief, project


class Editor:
    def __init__(self):
        self.history = []
        self.screens = []

    async def next_search_plan(self, brief, project, *, history):
        self.history.append(deepcopy(history))
        index = len(history)
        return ModelResult(value=SourceSearchPlan(queries=[SubjectSearchQuery(
            subject_index=0, query=f'English interview {index}', provider='you')],
            editorial_coverage=['Experiences']), usage=usage('source_search_replan'))

    async def judge_candidate(self, *args, **kwargs):
        return ModelResult(value=SourceCandidateJudgment(decision='acquire',
            covered_subject_indexes=[0]), usage=usage('source_candidate_judgment'))

    async def inspect_excerpt(self, brief, project, source, excerpt):
        self.screens.append(excerpt)
        return ModelResult(value=SourcePromiseHint(promising='REAL_ACCOUNT' in excerpt,
            reason='Generic theory lacks the requested perspective' if 'REAL_ACCOUNT' not in excerpt
            else 'Contains a first-person account'), usage=usage('source_promise_hint'))


class Discovery:
    def __init__(self):
        self.queries = []

    def discover_query(self, query, provider):
        self.queries.append(query)
        return [candidate(candidate_id=sha256(query.encode()).hexdigest(),
                          title=query, url=f'https://example.org/{len(self.queries)}')]


class Acquirer:
    def __init__(self, text_for):
        self.calls = []
        self.text_for = text_for

    def acquire(self, source):
        self.calls.append(source.title)
        text = self.text_for(source)
        return AcquisitionPayload(record=AcquiredSource(candidate=source, status='acquired',
            normalized_text=text, normalized_text_sha256=sha256(text.encode()).hexdigest()))


async def test_replans_across_languages_beyond_four_without_digesting_bad_sources():
    editor, discovery = Editor(), Discovery()
    acquirer = Acquirer(lambda c: 'REAL_ACCOUNT' if c.title.endswith('5') else 'GENERIC_THEORY')
    digested = []
    async def accept(payload):
        digested.append(payload.record.candidate.title)
        return True
    result = await ContributionResearch(editor, discovery, acquirer).run(*context(), accept_acquired=accept)
    assert len(discovery.queries) == 6
    assert digested == ['English interview 5']
    assert result.selection.coverage_complete
    assert editor.history[1][0]['results'][0]['rejection'] == 'Generic theory lacks the requested perspective'
    assert all('English' in query for query in discovery.queries)


async def test_reading_continues_to_late_evidence_and_resume_reuses_acquisition_and_triage():
    editor, discovery = Editor(), Discovery()
    acquirer = Acquirer(lambda c: 'background ' * 2000 + 'REAL_ACCOUNT')
    state, calls = {}, []
    async def load():
        return deepcopy(state) if state else None
    async def save(value):
        state.clear()
        state.update(deepcopy(value))
    async def accept(payload):
        calls.append(payload)
        if len(calls) == 1:
            raise RuntimeError('temporary editorial provider outage')
        return True
    research = ContributionResearch(editor, discovery, acquirer)
    with pytest.raises(RuntimeError, match='temporary'):
        await research.run(*context(), accept_acquired=accept, load_progress=load, save_progress=save)
    screened = len(editor.screens)
    assert screened > 1
    await research.run(*context(), accept_acquired=accept, load_progress=load, save_progress=save)
    assert len(acquirer.calls) == len(discovery.queries) == 1
    assert len(editor.screens) == screened


async def test_verified_lead_is_used_without_repeating_search():
    editor, discovery = Editor(), Discovery()
    acquirer = Acquirer(lambda c: 'REAL_ACCOUNT')
    async def accept(payload):
        return True
    result = await ContributionResearch(editor, discovery, acquirer).run(
        *context(), accept_acquired=accept, seed_candidates=[candidate()])
    assert not discovery.queries
    assert result.selection.coverage_complete

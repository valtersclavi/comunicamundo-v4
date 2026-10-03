from copy import deepcopy
import json

import pytest

from comunicamundo_v4.hefesto.atomic import AtomicCommand, generate_atomic
from comunicamundo_v4.hefesto.batching import conservative_tokens
from comunicamundo_v4.hefesto.contracts import (
    CallUsage, ContentReviewProposal, FinalTextProposal, NarrativeArchitecture,
    PreservationReviewProposal, SourceCandidateJudgment,
)
from comunicamundo_v4.hefesto.gateway import ModelCallError, ModelResult
from comunicamundo_v4.hefesto.service import Hefesto
from fakes import MemoryRepository
from test_product_architecture import request


def usage(task, status='succeeded'):
    return CallUsage(task_type=task, prompt_tokens=10, output_tokens=5,
                     total_tokens=15, reasoning_tokens=0, latency_ms=1,
                     rated_cost_usd='0.0001', status=status)


class PagedProvider:
    """A deterministic provider that enforces small physical responses."""
    def __init__(self, value, *, output_limit=420, fail_after=None, atomic_fail_once=False):
        self.value = value
        self.serialized = value.model_dump_json()
        self.output_limit = output_limit
        self.fail_after = fail_after
        self.atomic_fail_once = atomic_fail_once
        self.calls = []
        self.writes = []
        self.windows = []
        self.initial_calls = 0

    async def count_tokens(self, text):
        return conservative_tokens(text)

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        if kwargs['schema'] is not AtomicCommand:
            self.initial_calls += 1
            raise ModelCallError('provider output ended with MAX_TOKENS', usage(kwargs['task_type'], 'failed'))
        state = json.loads(kwargs['prompt'].split('ATOMIC STATE:\n', 1)[1])
        self.windows.append(state['input_window'])
        if self.fail_after is not None and len(self.writes) >= self.fail_after:
            self.fail_after = None
            raise ModelCallError('temporary provider outage', usage(kwargs['task_type'], 'failed'))
        if self.atomic_fail_once:
            self.atomic_fail_once = False
            raise ModelCallError('MAX_TOKENS', usage(kwargs['task_type'], 'failed'))
        current = state['input_window']
        if current['end'] < state['input_length']:
            command = AtomicCommand(action='read_input', start=current['end'])
        elif state['first_unread_offset'] is not None and state['first_unread_offset'] < current['start']:
            command = AtomicCommand(action='read_input', start=state['first_unread_offset'])
        elif state['output_length'] < len(self.serialized):
            remaining = self.serialized[state['output_length']:]
            piece = remaining[:state['next_write_target_characters']]
            if len(piece) > self.output_limit:
                raise ModelCallError('MAX_TOKENS', usage(kwargs['task_type'], 'failed'))
            self.writes.append((state['output_length'], piece))
            command = AtomicCommand(action='append', text=piece)
        else:
            command = AtomicCommand(action='finish')
        return ModelResult(value=command, usage=usage(kwargs['task_type']))


@pytest.mark.parametrize('task,schema,value', [
    ('content_draft', FinalTextProposal, FinalTextProposal(title='Title', body='A complete idea with its limits. ' * 80 + 'End.')),
    ('content_revision', FinalTextProposal, FinalTextProposal(title='Title', body='A corrected argument. ' * 80 + 'End.')),
    ('product_styling', FinalTextProposal, FinalTextProposal(title='Title', body='Styled preserved content. ' * 80 + 'End.')),
    ('style_preservation_repair', FinalTextProposal, FinalTextProposal(title='Title', body='Repaired content. ' * 80 + 'End.')),
    ('final_narrative_edit', FinalTextProposal, FinalTextProposal(title='Title', body='A narrative with “quotes”, \\ and á😊. ' * 80 + 'End.')),
    ('content_review', ContentReviewProposal, ContentReviewProposal(approved=False, summary='Review.', issues=[
        {'severity':'important', 'location':f'paragraph {i}', 'problem':'A needed qualification is absent.',
         'required_correction':'Restore the original qualification.'} for i in range(20)])),
    ('final_verification', PreservationReviewProposal, PreservationReviewProposal(approved=False, summary='Preserve meaning.', differences=[f'Important distinct difference {i}' for i in range(80)])),
    ('final_reverification', PreservationReviewProposal, PreservationReviewProposal(approved=True, summary='All preserved.', differences=[])),
    ('narrative_architecture', NarrativeArchitecture, NarrativeArchitecture(opening='Open.', governing_question='Why?', progression=[f'Progression {i}' for i in range(100)], interweavings=['Connections.'], turning_point='Reframe.', closing='Resolve.', non_negotiables=['Preserve meaning.'])),
])
async def test_every_production_schema_can_complete_after_output_limit(task, schema, value):
    repo = MemoryRepository()
    provider = PagedProvider(value)
    service = Hefesto(repo, provider)
    run = await service.prepare(request())
    result = await service._call(run, task_type=task, prompt='Perform the complete authorized editorial task.',
                                 schema=schema, max_output_tokens=2048, thinking_tokens=0)
    assert result.value == value
    assert provider.initial_calls == 1
    assert len(provider.writes) >= 1
    assert ''.join(piece for _, piece in provider.writes) == provider.serialized
    assert len(repo.usage[run.execution_run_id]) == len(provider.calls)
    assert result.usage.total_tokens == len(provider.calls) * 15
    assert all(call.task_type == task for call in repo.usage[run.execution_run_id])
    assert any(state['complete'] for state in repo.atomic_states.values())


async def test_large_input_is_read_in_bounded_windows_without_discarding_originals():
    value = SourceCandidateJudgment(decision='acquire', covered_subject_indexes=[0, 1])
    provider = PagedProvider(value)
    original = 'Original briefing and evidence: á😊 "quoted".\n' * 500
    result = await generate_atomic(provider, task_type='source_candidate_judgment', prompt=original,
                                   schema=type(value), system_instruction='Follow the user brief.',
                                   max_output_tokens=2048, thinking_tokens=0, input_budget=5000)
    assert result.value == value
    assert provider.initial_calls == 0
    covered = set()
    for window in provider.windows:
        assert window['text'] == original[window['start']:window['end']]
        covered.update(range(window['start'], window['end']))
    assert len(covered) == len(original)
    assert all([await provider.count_tokens(call['system_instruction'] + call['prompt'] +
               json.dumps(AtomicCommand.model_json_schema())) <= 5000 for call in provider.calls])


async def test_checkpoint_resumes_output_without_regenerating_completed_fragments():
    value = FinalTextProposal(title='Resume', body='Keep every quotation and qualification. ' * 100 + 'End.')
    provider = PagedProvider(value, fail_after=2)
    store = {}
    async def load(key):
        return deepcopy(store.get(key))
    async def save(key, state):
        store[key] = deepcopy(state)
    kwargs = dict(task_type='final_narrative_edit', prompt='Edit the full piece.', schema=type(value),
                  system_instruction='Preserve distinct content.', max_output_tokens=2048,
                  thinking_tokens=0, load=load, save=save)
    with pytest.raises(ModelCallError, match='temporary provider outage'):
        await generate_atomic(provider, **kwargs)
    checkpoint = next(iter(store.values()))
    assert checkpoint['output'] and not checkpoint['complete']
    written = checkpoint['output']
    write_count = len(provider.writes)
    result = await generate_atomic(provider, **kwargs)
    assert result.value == value
    assert provider.initial_calls == 1
    assert provider.writes[write_count][0] == len(written)
    assert ''.join(piece for _, piece in provider.writes) == provider.serialized
    # Source-side accounting includes attempts saved before the interruption.
    assert result.usage.total_tokens == len(provider.calls) * 15
    calls = len(provider.calls)
    again = await generate_atomic(provider, **kwargs)
    assert again.value == value
    assert len(provider.calls) == calls
    assert again.usage.total_tokens == 0


async def test_max_tokens_inside_an_atomic_write_reduces_task_size():
    value = FinalTextProposal(title='Title', body='Long literal output. ' * 50 + 'End.')
    provider = PagedProvider(value, atomic_fail_once=True)
    result = await generate_atomic(provider, task_type='content_draft', prompt='Develop the approved content.',
                                   schema=type(value), system_instruction='Keep content.',
                                   max_output_tokens=2048, thinking_tokens=0)
    assert result.value == value
    states = [json.loads(c['prompt'].split('ATOMIC STATE:\n',1)[1]) for c in provider.calls if c['schema'] is AtomicCommand]
    assert states[1]['next_write_target_characters'] < states[0]['next_write_target_characters']
    assert states[1]['output_length'] == states[0]['output_length'] == 0


async def test_incomplete_json_is_repaired_locally_before_returning_an_artifact():
    value = FinalTextProposal(title='Title', body='Preserve the complete argument.')
    class RepairProvider(PagedProvider):
        def __init__(self):
            super().__init__(value)
            self.phase = 0
            self.validation_feedback = None
        async def generate(self, **kwargs):
            if kwargs['schema'] is not AtomicCommand:
                return await super().generate(**kwargs)
            self.calls.append(kwargs)
            state = json.loads(kwargs['prompt'].split('ATOMIC STATE:\n', 1)[1])
            self.phase += 1
            if self.phase == 1:
                command = AtomicCommand(action='append', text=self.serialized[:-1])
            elif self.phase == 2:
                command = AtomicCommand(action='finish')
            elif self.phase == 3:
                self.validation_feedback = json.loads(state['feedback'])
                command = AtomicCommand(action='append', text='}')
            else:
                command = AtomicCommand(action='finish')
            return ModelResult(value=command, usage=usage(kwargs['task_type']))
    provider = RepairProvider()
    result = await generate_atomic(provider, task_type='content_revision', prompt='Correct the authorized points.',
                                   schema=type(value), system_instruction='Preserve content.',
                                   max_output_tokens=2048, thinking_tokens=0)
    assert result.value == value
    assert provider.validation_feedback
    assert provider.phase == 4


async def test_source_editor_uses_the_same_atomic_path_for_candidate_judgment():
    from comunicamundo_v4.hefesto.contracts import (
        NarrativeAxisProposal, ProvisionalEditorialProject, ProvisionalEditorialSubject,
    )
    from comunicamundo_v4.hefesto.source_editor import EditorialSourceEditor
    from test_source_pipeline import candidate
    value = SourceCandidateJudgment(decision='acquire', covered_subject_indexes=[0])
    provider = PagedProvider(value)
    repo = MemoryRepository()
    run = await Hefesto(repo, provider).prepare(request())
    project = ProvisionalEditorialProject(
        working_title='Title', editorial_promise='Promise',
        narrative_axis=NarrativeAxisProposal(statement='Axis', rationale='Reason'),
        duration_minutes=8, disclaimer='Needs evidence.',
        subjects=[ProvisionalEditorialSubject(title='Subject', editorial_role='Role',
                  desired_depth='Depth', allocated_minutes=8)],
    )
    result = await EditorialSourceEditor(provider, repository=repo,
        execution_run_id=run.execution_run_id).judge_candidate(
            run.request.brief, project, candidate(), eligible_subject_indexes=[0])
    assert result.value == value
    assert len(result.usages) == len(provider.calls)
    assert repo.atomic_states


def test_combining_editorial_understanding_does_not_cut_content_or_limits():
    from comunicamundo_v4.hefesto.contracts import EditorialCorpus, EditorialKnowledgeUnit
    from comunicamundo_v4.hefesto.service import _combined_governing_understanding, _combined_forbidden_inferences
    understanding = 'A meaningful distinct qualification. ' * 100
    corpus = EditorialCorpus(governing_understanding=understanding,
        units=[EditorialKnowledgeUnit(title='Unit', synthesis='Idea', essential_points=['Point'],
               evidence=[{'source_ordinals':[1], 'contribution':'Original support.'}])],
        forbidden_inferences=[f'Original limit {i}' for i in range(10)])
    addition = 'The complete additional qualification.'
    combined = _combined_governing_understanding(corpus, addition)
    assert combined == f'{corpus.governing_understanding} {addition}'.strip()
    limits = _combined_forbidden_inferences(corpus, ['New limit', 'Original limit 0'])
    assert limits == [*corpus.forbidden_inferences, 'New limit']


async def test_malformed_atomic_command_preserves_written_prefix_and_recovers():
    value = FinalTextProposal(title='Quotes', body='A family says "keep every detail". ' * 30)
    class MalformedProvider(PagedProvider):
        malformed_count = 0
        async def generate(self, **kwargs):
            if kwargs['schema'] is AtomicCommand and self.writes and self.malformed_count < 4:
                self.calls.append(kwargs)
                self.malformed_count += 1
                raise ModelCallError('invalid AtomicCommand: invalid JSON', usage(kwargs['task_type'], 'failed'),
                                     malformed=True, response_text='{"action":"append","text":"bad "quote"}')
            return await super().generate(**kwargs)
    provider = MalformedProvider(value)
    store = {}
    async def save(key, state):
        store[key] = deepcopy(state)
    result = await generate_atomic(provider, task_type='source_enrichment_assessment', prompt='Assess the full contribution.',
        schema=type(value), system_instruction='Preserve all content.', max_output_tokens=2048, thinking_tokens=0, save=save)
    assert result.value == value
    assert provider.malformed_count == 4
    assert ''.join(piece for _, piece in provider.writes) == provider.serialized
    assert result.usage.total_tokens == len(provider.calls) * 15
    assert next(iter(store.values()))['complete']


async def test_malformed_normal_response_is_repaired_without_repeating_the_full_task():
    value = FinalTextProposal(title='Title', body='The intact completed argument.')
    class RepairProvider(PagedProvider):
        async def generate(self, **kwargs):
            if kwargs['schema'] is not AtomicCommand:
                self.calls.append(kwargs)
                self.initial_calls += 1
                raise ModelCallError('invalid FinalTextProposal: incomplete JSON', usage(kwargs['task_type'], 'failed'),
                                     malformed=True, response_text=self.serialized[:-1])
            return await super().generate(**kwargs)
    provider = RepairProvider(value)
    result = await generate_atomic(provider, task_type='editorial_subject_reading', prompt='Read originals.',
        schema=type(value), system_instruction='Use references.', max_output_tokens=2048, thinking_tokens=0,
        split_by_caller=True)
    assert result.value == value
    assert provider.initial_calls == 1
    assert provider.writes == [(len(provider.serialized)-1, '}')]
    assert result.usage.total_tokens == len(provider.calls) * 15


async def test_completed_ordinary_task_is_durable_and_does_not_consume_again():
    value = SourceCandidateJudgment(decision='acquire', covered_subject_indexes=[0])
    class SmallProvider:
        calls = 0
        async def count_tokens(self, text):
            return 1
        async def generate(self, **kwargs):
            self.calls += 1
            return ModelResult(value=value, usage=usage(kwargs['task_type']))
    provider, store = SmallProvider(), {}
    async def load(key):
        return deepcopy(store.get(key))
    async def save(key, state):
        store[key] = deepcopy(state)
    kwargs = dict(task_type='source_candidate_judgment', prompt='Consider this candidate.', schema=type(value),
                  system_instruction='Respect the promise.', max_output_tokens=2048, thinking_tokens=0, load=load, save=save)
    assert (await generate_atomic(provider, **kwargs)).value == value
    repeated = await generate_atomic(provider, **kwargs)
    assert repeated.value == value and repeated.usage.total_tokens == 0
    assert provider.calls == 1
    # A deliberate new editorial correction can request a fresh completed task.
    await generate_atomic(provider, reuse_complete=False, **kwargs)
    assert provider.calls == 2

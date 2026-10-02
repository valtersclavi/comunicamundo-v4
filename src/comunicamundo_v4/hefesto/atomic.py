"""Bounded model I/O. Page originals and edit the result; never summarize to fit."""
from __future__ import annotations

import json
import re
from collections.abc import Awaitable, Callable
from decimal import Decimal
from hashlib import sha256
from typing import Annotated, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, StringConstraints, ValidationError

from .batching import conservative_tokens
from .contracts import CallUsage
from .gateway import ModelCallError, ModelGateway, ModelResult

T = TypeVar('T', bound=BaseModel)
Load = Callable[[str], Awaitable[dict | None]]
Save = Callable[[str, dict], Awaitable[None]]
Record = Callable[[CallUsage], Awaitable[None]]


class AtomicCommand(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['read_input', 'read_output', 'append', 'replace', 'finish']
    start: int = 0
    end: int = 0
    text: Annotated[str, StringConstraints(strip_whitespace=False)] = ''


def _total(task: str, calls: list[CallUsage]) -> CallUsage:
    return CallUsage(
        task_type=task,
        **{name: sum(getattr(call, name) for call in calls) for name in (
            'prompt_tokens', 'cached_tokens', 'output_tokens', 'reasoning_tokens',
            'total_tokens', 'latency_ms',
        )},
        rated_cost_usd=str(sum((Decimal(c.rated_cost_usd or '0') for c in calls), Decimal('0'))),
        status='succeeded',
    )


def _cover(ranges: list[list[int]], start: int, end: int) -> list[list[int]]:
    result: list[list[int]] = []
    for a, b in sorted([*ranges, [start, end]]):
        if result and a <= result[-1][1]:
            result[-1][1] = max(result[-1][1], b)
        else:
            result.append([a, b])
    return result


def _unread(ranges: list[list[int]], size: int) -> int | None:
    cursor = 0
    for a, b in ranges:
        if a > cursor:
            return cursor
        cursor = max(cursor, b)
    return cursor if cursor < size else None


def _window(text: str, start: int, tokens: int) -> str:
    """A processing window, not a shortened replacement for the original object."""
    low, high = 0, len(text) - start
    while low < high:
        mid = (low + high + 1) // 2
        if conservative_tokens(text[start:start + mid]) <= tokens:
            low = mid
        else:
            high = mid - 1
    return text[start:start + max(1, low)]


def _size_failure(error: Exception) -> bool:
    message = str(error).upper()
    if "429" in message or "RESOURCE_EXHAUSTED" in message:
        return False
    return ("TOKEN" in message and ("EXCEED" in message or "TOO LONG" in message)) or any(marker in message for marker in (
        'MAX_TOKENS', 'MAX_OUTPUT_TOKENS', 'CONTEXT_LENGTH_EXCEEDED',
        'INPUT TOKEN COUNT EXCEEDS', 'CONTEXT WINDOW',
    ))


async def generate_atomic(
    gateway: ModelGateway, *, task_type: str, prompt: str, schema: type[T],
    system_instruction: str, max_output_tokens: int, thinking_tokens: int,
    input_budget: int = 64_000, load: Load | None = None, save: Save | None = None,
    record: Record | None = None, split_by_caller: bool = False,
) -> ModelResult[T]:
    """Keep ordinary small calls unchanged; page any oversized task/result.

    The original request and editable JSON result are separate addressable objects.
    Output fragments are transport pieces, not separate editorial answers to merge.
    Only a complete schema-validated result is returned to the editorial pipeline.
    """
    schema_text = json.dumps(schema.model_json_schema(), ensure_ascii=False)
    key = sha256(('atomic-v1:' + task_type + system_instruction + prompt + schema_text).encode()).hexdigest()
    state = await load(key) if load else None
    calls: list[CallUsage] = []

    async def attempt(**kwargs):
        try:
            result = await gateway.generate(**kwargs)
        except ModelCallError as error:
            if error.usage:
                usage = error.usage.model_copy(update={'task_type': task_type, 'status': 'failed', 'error_message': str(error)})
                calls.append(usage)
                if record:
                    await record(usage)
                if state is not None:
                    state.setdefault('usages', []).append(usage.model_dump(mode='json'))
                    await checkpoint()
            raise
        usage = result.usage.model_copy(update={'task_type': task_type})
        calls.append(usage)
        if record:
            await record(usage)
        if state is not None:
            state.setdefault('usages', []).append(usage.model_dump(mode='json'))
            await checkpoint()
        return result

    async def checkpoint():
        if save:
            await save(key, state)

    if state and state.get('complete'):
        return ModelResult(value=schema.model_validate_json(state['output']),
                           usage=_total(task_type, calls), usages=tuple(calls))

    try:
        if state is None:
            # Already semantic packet tasks retain their domain-specific splitter.
            fits = await gateway.count_tokens(system_instruction + schema_text + prompt) <= input_budget
            words = re.search(r'(?:approximately|approximate target of)\s+(\d+)\s+words', prompt)
            expected_long = schema.__name__ == 'FinalTextProposal' and words is not None and int(words.group(1)) * 3 > max_output_tokens - thinking_tokens
            if fits and not expected_long:
                try:
                    return_value = await attempt(
                        task_type=task_type, prompt=prompt, schema=schema,
                        system_instruction=system_instruction,
                        max_output_tokens=max_output_tokens, thinking_tokens=thinking_tokens,
                    )
                    return ModelResult(value=return_value.value, usage=_total(task_type, calls), usages=tuple(calls))
                except ModelCallError as error:
                    if split_by_caller or not _size_failure(error):
                        raise
            state = {'output': '', 'ranges': [], 'input_start': 0, 'output_start': 0,
                     'segment_chars': max(1, min(8192, max_output_tokens * 2)),
                     'window_tokens': max(1, input_budget // 4), 'feedback': '',
                     'complete': False, 'last_action': '', 'repetitions': 0,
                     'usages': [usage.model_dump(mode='json') for usage in calls], 'seen_actions': {}}
            await checkpoint()

        protocol = '''Execute the original editorial task through bounded read/write operations.
The complete original request remains available at character offsets. It has
NOT been summarized or shortened. Read all its windows before finishing; reread
any window whenever needed. Follow its editorial instructions, brief, approved
scope and narrative freedom. Transport boundaries are NOT editorial sections.
Build ONE JSON result matching OUTPUT SCHEMA. Use append for the next exact JSON
fragment, or replace for a local correction/reordering of the work in progress.
Do not generate a separate title, introduction or conclusion per fragment.
Strings and arrays may span any number of writes. There is no total length cap.
Output text is literal JSON, no Markdown fences. Preserve quotations and data.
You may read any earlier output window before editing it. Never summarize an
original to make it fit. Plan narrative continuity across the complete result.
One command per call. read_input/read_output use start; a bounded window follows.
append adds text verbatim. replace changes output[start:end], with end exclusive.
finish requests validation of the complete JSON; use it only after completing
the WHOLE task. Offsets count Unicode characters, not bytes. Local writes and
reads may repeat as necessary; do not repeatedly issue an unchanged no-op.
'''
        while True:
            input_start = state['input_start']
            original = _window(prompt, input_start, state['window_tokens'])
            output = state['output']
            output_start = min(state['output_start'], len(output))
            working = _window(output, output_start, state['window_tokens']) if output else ''
            frame = _window(prompt, 0, max(1, state['window_tokens'] // 4))
            payload = {
                'task': task_type, 'input_length': len(prompt),
                'first_unread_offset': _unread(state['ranges'], len(prompt)),
                'original_task_frame': frame,
                'input_window': {'start': input_start, 'end': input_start + len(original), 'text': original},
                'output_length': len(output),
                'output_window': {'start': output_start, 'end': output_start + len(working), 'text': working},
                'next_write_target_characters': state['segment_chars'],
                'feedback': state['feedback'],
            }
            atomic_prompt = 'OUTPUT SCHEMA:\n' + schema_text + '\nATOMIC STATE:\n' + json.dumps(payload, ensure_ascii=False)
            atomic_system = system_instruction + '\n' + protocol
            tokens = await gateway.count_tokens(atomic_system + atomic_prompt + json.dumps(AtomicCommand.model_json_schema()))
            if tokens > input_budget:
                if state['window_tokens'] <= 1:
                    # Schema/system overhead is not a source object; report honestly.
                    raise ModelCallError('Atomic protocol/schema exceeds provider input budget')
                state['window_tokens'] = max(1, state['window_tokens'] // 2)
                continue
            try:
                response = await attempt(
                    task_type=task_type + ':atomic', prompt=atomic_prompt,
                    schema=AtomicCommand, system_instruction=atomic_system,
                    max_output_tokens=max(1024, max_output_tokens),
                    thinking_tokens=min(thinking_tokens, max(1024, max_output_tokens) // 4),
                )
            except ModelCallError as error:
                if not _size_failure(error):
                    await checkpoint()
                    raise
                if state['segment_chars'] == 1 and state['window_tokens'] == 1:
                    raise ModelCallError('Provider cannot return a minimal atomic command', usages=tuple(calls)) from error
                state['segment_chars'] = max(1, state['segment_chars'] // 2)
                state['window_tokens'] = max(1, state['window_tokens'] // 2)
                state['feedback'] = 'Previous response hit provider limit. Produce a smaller operation, preserving all remaining work.'
                await checkpoint()
                continue
            command = response.value
            previous_ranges = [pair[:] for pair in state['ranges']]
            state['ranges'] = _cover(state['ranges'], input_start, input_start + len(original))
            state['ranges'] = _cover(state['ranges'], 0, len(frame))
            signature = sha256((command.model_dump_json() + output + str(state['ranges'])).encode()).hexdigest()
            seen = state.setdefault('seen_actions', {})
            seen[signature] = seen.get(signature, 0) + 1
            state['repetitions'] = seen[signature] - 1
            state['last_action'] = signature
            if state['repetitions'] >= 3:
                await checkpoint()
                raise ModelCallError('Atomic model repeated an unchanged operation without progress')
            state['feedback'] = ''
            if command.action == 'read_input':
                if 0 <= command.start < len(prompt):
                    state['input_start'] = command.start
                else:
                    state['feedback'] = 'Input offset is outside the available original.'
            elif command.action == 'read_output':
                if 0 <= command.start <= len(output):
                    state['output_start'] = command.start
                else:
                    state['feedback'] = 'Output offset is outside the current result.'
            elif command.action == 'append':
                state['output'] += command.text
                state['output_start'] = max(0, len(state['output']) - state['segment_chars'])
            elif command.action == 'replace':
                if 0 <= command.start <= command.end <= len(output):
                    state['output'] = output[:command.start] + command.text + output[command.end:]
                    state['output_start'] = command.start
                else:
                    state['feedback'] = 'Replacement offsets must address the current output.'
            else:
                unread = _unread(state['ranges'], len(prompt))
                if unread is not None:
                    state['input_start'] = unread
                    state['feedback'] = 'Original input remains unread. Read it and verify the result before finishing.'
                else:
                    try:
                        value = schema.model_validate_json(state['output'])
                    except ValidationError as error:
                        state['feedback'] = json.dumps([
                            {'path': list(e['loc']), 'type': e['type']} for e in error.errors()
                        ])
                    else:
                        state['complete'] = True
                        await checkpoint()
                        reported = calls if record else [CallUsage.model_validate(u) for u in state.get('usages', [])]
                        return ModelResult(value=value, usage=_total(task_type, reported), usages=tuple(reported))
            if state['output'] != output or state['ranges'] != previous_ranges:
                state['seen_actions'] = {}
            await checkpoint()
    except ModelCallError as error:
        # Propagate physical-call accounting, including failed oversized attempts.
        error.usages = tuple(calls)
        raise

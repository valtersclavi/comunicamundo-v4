"""Locate real leads before presenting a proposed external contribution."""
from __future__ import annotations

import asyncio
from hashlib import sha256
import os
from .contracts import ProvisionalEditorialProject, ProvisionalEditorialSubject, SourceCandidate
from .source_editor import EditorialSourceEditor
from .source_pipeline import SourceDiscovery, YouSearchProvider, OpenAlexSearchProvider


def lead_key(brief, opportunity):
    return sha256(("source-leads-v1:" + brief.model_dump_json() + opportunity).encode()).hexdigest()


def source_discovery():
    return SourceDiscovery([
        YouSearchProvider(os.environ["YOU_API_KEY"]),
        OpenAlexSearchProvider(os.environ["OPENALEX_API_KEY"]),
    ])


async def verify_leads(run, opportunity, axis, gateway, repository):
    key = lead_key(run.request.brief, opportunity)
    state = await repository.atomic_task(run.execution_run_id, key) or {}
    if state.get("complete"):
        return [SourceCandidate.model_validate(c) for c in state["leads"]]
    editor = EditorialSourceEditor(gateway, repository=repository, execution_run_id=run.execution_run_id)
    brief = run.request.brief.model_copy(update={
        "approach": "MANDATORY BRIEF ADDENDUM — locate evidence for: " + opportunity
                    + "\nORIGINAL CONTEXT: " + run.request.brief.approach,
    })
    project = ProvisionalEditorialProject(
        working_title=opportunity, editorial_promise=opportunity, narrative_axis=axis,
        duration_minutes=1, subjects=[ProvisionalEditorialSubject(
            title=opportunity, editorial_role=opportunity, desired_depth=opportunity,
            allocated_minutes=1,
        )], disclaimer="Verify discoverable evidence before offering this opportunity.",
    )

    async def save():
        await repository.save_atomic_task(run.execution_run_id, key, state)

    async def record(result):
        for call in result.usages or (result.usage,):
            await repository.record_usage(run.execution_run_id, run.project_id, call)

    if "plan" not in state:
        result = await editor.search_plan(brief, project)
        await record(result)
        state["plan"] = result.value.model_dump(mode="json")
        await save()
    discovery = source_discovery()
    for index, query in enumerate(state["plan"]["queries"]):
        batches = state.setdefault("batches", {})
        if str(index) not in batches:
            candidates = await asyncio.to_thread(discovery.discover_query, query["query"], query["provider"])
            batches[str(index)] = [c.model_dump(mode="json") for c in candidates]
            await save()
        for raw in batches[str(index)]:
            candidate = SourceCandidate.model_validate(raw)
            judgments = state.setdefault("judgments", {})
            if candidate.candidate_id not in judgments:
                result = await editor.judge_candidate(brief, project, candidate, eligible_subject_indexes=[0])
                await record(result)
                judgments[candidate.candidate_id] = result.value.model_dump(mode="json")
                await save()
            judgment = judgments[candidate.candidate_id]
            if judgment["decision"] == "acquire" and 0 in judgment["covered_subject_indexes"]:
                state.update(complete=True, leads=[raw])
                await save()
                return [candidate]
    # This is an unoffered proposal: reject it before making a promise to the user.
    state.update(complete=True, leads=[])
    await save()
    return []

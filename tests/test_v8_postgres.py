"""Real separate-session recovery/exclusion contract; CI uses an isolated DB."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from comunicamundo_v8.hefesto.postgres import ProductRepository
from comunicamundo_v8.hefesto.v8.repository import Repository, Busy
from comunicamundo_v8.hefesto.v8.contracts import (Input, State, EditorialProject,
    InternalPlanning, EditorialFields, Extent, GeneratedWork, EditorialStage, identity)
from test_v8 import conception


@pytest.mark.asyncio
async def test_postgres_independent_sessions_lock_cas_and_restart_journal():
    url=os.environ.get("V8_TEST_POSTGRES_URL")
    if not url:pytest.skip("isolated PostgreSQL integration database not configured")
    first=ProductRepository(url);second=ProductRepository(url)
    await first.open();await second.open()
    try:
        async with first._pool.connection() as c:
            await c.execute("CREATE SCHEMA IF NOT EXISTS hefesto_product")
            await c.execute((Path(__file__).parents[1]/"migrations/1008_hefesto_v8.sql").read_text())
        a,b=Repository(first),Repository(second)
        request=Input(brief=dict(theme="Generic",approach="Explain",audience="General",
            product="podcast",duration_minutes=1,output_language="en"))
        state=State(execution_id=uuid4(),owner_id="fixture",request=request,
            action=dict(kind="plan",id="initial"))
        await a.create(state)
        async with a.lock(state.execution_id):
            with pytest.raises(Busy):
                async with b.lock(state.execution_id):pytest.fail("overlapping worker acquired project")
            stale=await b.get(state.execution_id)
            state.projects=[EditorialProject(version=1,document="Preserved proposal",corpus_manifest_id="manifest")]
            plan=InternalPlanning(planning_id=uuid4(),version=1,
                briefing_hash=identity(request.brief),corpus_manifest_id="manifest",
                content="Explain a mechanism, develop its consequences and a counterargument, then reach a qualified conclusion.",
                extent=Extent(target_words=150,duration_minutes=1),content_kind="intellectual_draft",draft_target_words=195,measured_words=18,approved_at="2026-10-08T00:00:00+00:00")
            state.internal_plans=[plan]
            state.projects.append(EditorialProject(version=2,corpus_manifest_id="manifest",
                planning_id=plan.planning_id,planning_version=1,approved_at=plan.approved_at,
                fields=EditorialFields(central_idea="Explain a mechanism.",
                    narrative_development="Develop consequences and a counterargument.",
                    intended_conclusion="Reach a qualified understanding.")))
            state.work=GeneratedWork(title="Work",body="A preserved valid passage.")
            state.review_checkpoint=dict(base_work=state.work.model_dump(mode="json"),
                groups=[dict(start=0,end=len(state.work.body),issues=[])],next_group=0)
            state.planning_checkpoint={}
            state.writing_checkpoint=dict(complete=True,attempts=[])
            state.editorial_stages=[EditorialStage(position=1,stage_id="completeness",status="approved",
                examined_work=state.work,approval=dict(work_hash=identity(state.work),consolidated_work=state.work.model_dump(mode="json"))),
                EditorialStage(position=2,stage_id="consistency",status="reconsidering",
                    attempts=[dict(number=1,confirmation=dict(resolved=False)),dict(number=2,confirmation=dict(resolved=False))],
                    reconsideration=dict(before=state.work.model_dump(mode="json")))]
            await a.save(state)
            with pytest.raises(Busy):await b.save(stale)
            key=identity("generic-operation")
            await a.save_operation(state.execution_id,key,dict(raw="{malformed",in_flight=False,pending=True))
        # Closing the original worker/pool cannot erase the durable checkpoint.
        await first.close()
        async with b.lock(state.execution_id):
            recovered=await b.get(state.execution_id)
            assert recovered.projects[0].document=="Preserved proposal"
            assert recovered.internal_plans[0]==plan
            assert recovered.projects[1].planning_id==plan.planning_id
            assert recovered.projects[1].approved_at==plan.approved_at
            assert recovered.work.body=="A preserved valid passage."
            assert recovered.review_checkpoint==state.review_checkpoint
            assert recovered.planning_checkpoint==state.planning_checkpoint
            assert recovered.writing_checkpoint==state.writing_checkpoint
            assert recovered.editorial_stages==state.editorial_stages
            assert (await b.operation(state.execution_id,key))["raw"]=="{malformed"
            assert recovered.action["id"]=="initial"
    finally:
        await first.close();await second.close()


"""Real separate-session recovery/exclusion contract; CI uses an isolated DB."""
import os
from pathlib import Path
from uuid import uuid4
import pytest
from comunicamundo_v4.hefesto.postgres import ProductRepository
from comunicamundo_v4.hefesto.v8.repository import Repository, Busy
from comunicamundo_v4.hefesto.v8.contracts import Input, State, EditorialProject, identity


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
            await a.save(state)
            with pytest.raises(Busy):await b.save(stale)
            key=identity("generic-operation")
            await a.save_operation(state.execution_id,key,dict(raw="{malformed",in_flight=False,pending=True))
        # Closing the original worker/pool cannot erase the durable checkpoint.
        await first.close()
        async with b.lock(state.execution_id):
            recovered=await b.get(state.execution_id)
            assert recovered.projects[0].document=="Preserved proposal"
            assert (await b.operation(state.execution_id,key))["raw"]=="{malformed"
            assert recovered.action["id"]=="initial"
    finally:
        await first.close();await second.close()

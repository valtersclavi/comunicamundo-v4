"""Surplus intellectual draft contract and durable transport, no real inference."""
from dataclasses import replace
import json

import pytest

from test_v8 import Memory, Provider, Ingestor, Search, source, state, conception, review_ok, run
from comunicamundo_v8.hefesto.v8.contracts import State, InternalPlanning, identity
from comunicamundo_v8.hefesto.v8.engine import Editor
from comunicamundo_v8.hefesto.v8.provider import Calls, Recoverable
from comunicamundo_v8.hefesto.v8.runtime import public


@pytest.mark.asyncio
@pytest.mark.parametrize("word_count", [1646, 3900, 5200])
async def test_3000_word_commission_requests_3900_and_only_measures_received_draft(word_count):
    st=state(sources=[source()]);st.request.brief.duration_minutes=20
    # Synthetic distinct words exercise lossless transport, not literary quality.
    draft=" ".join(f"idea{i}" for i in range(word_count))+"\n\nEND_OF_DRAFT"
    body=" ".join(f"final{i}" for i in range(3000))
    repo=Memory()
    class Inspect(Provider):
        async def generate(self,prompt,system,output):
            assert output==65536
            if "COMPLETE PERSISTED INTELLECTUAL DRAFT" in prompt:
                saved=State.model_validate_json((await repo.get(st.execution_id)).model_dump_json())
                assert saved.internal_plans[0].content==draft
                assert saved.internal_plans[0].measured_words==word_count+1
                assert not saved.projects
            return await super().generate(prompt,system,output)
    p=Inspect([dict(body=draft),conception()[1]],output_tokens=65536)
    p.cap=replace(p.cap,thinking_budget_max=24576)
    editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st)
    assert len(p.prompts)==2
    system=p.prompts[0][1]
    assert "3000 palavras" in system and "3900 palavras" in system
    assert "MÉTODO DE COMPOSIÇÃO" in system and "causas, mecanismos ou consequências" in system
    assert "Não invente evidências" in system
    assert not st.planning_checkpoint
    plan=st.internal_plans[0]
    assert plan.draft_target_words==3900 and plan.extent.target_words==3000
    assert not plan.movements and plan.sufficiency is None
    assert public(st)["usage"]["call_count"]==2  # Word measurements are not calls.
    physical=[v for v in st.telemetry if "physical_call" in v]
    assert [v["stage"] for v in physical]==["intellectual-draft","editorial-project"]
    assert all(all(k in v for k in ("input_tokens","output_tokens","latency_ms","cost_usd")) for v in physical)
    assert next(v for v in st.telemetry if v.get("kind")=="artifact")["measured_words"]==word_count+1
    p.outputs=[dict(title="Work",body=body),*review_ok()]
    st.action=dict(kind="approve",id="approve",version=1)
    await editor.execute(st,Calls(repo,p,st))
    writer,contract=p.prompts[2]
    assert json.loads(writer.split("APPROVED COMPLETE INTELLECTUAL DRAFT (RAW MATERIAL):\n")[1].split("\nAPPROVED PE:")[0])["content"]==draft
    assert "3000 words" in writer and "no obligation to preserve" in contract
    assert st.work.body==body and st.status=="text_ready"
    assert len(st.editorial_stages)==4 and all(s.status=="approved" for s in st.editorial_stages)
    assert len(p.prompts)==7


@pytest.mark.asyncio
async def test_completed_raw_draft_is_reused_if_state_checkpoint_fails():
    class CrashBeforeDraftSave(Memory):
        crash=True
        async def save(self,st):
            if st.internal_plans and self.crash:
                self.crash=False
                raise SystemExit("worker terminated before artifact checkpoint")
            await super().save(st)
    repo=CrashBeforeDraftSave();st=state(sources=[source()]);p=Provider(conception())
    editor=Editor(repo,p,Ingestor([]),Search(source()))
    await repo.create(st)
    with pytest.raises(SystemExit):await editor.execute(st,Calls(repo,p,st))
    recovered=await repo.get(st.execution_id)
    assert not recovered.internal_plans
    raw=next(iter(repo.ops.values()))
    assert raw["complete"] and raw["value"]["body"]
    await editor.execute(recovered,Calls(repo,p,recovered))
    assert len(p.prompts)==2 and len(recovered.internal_plans)==1
    assert recovered.internal_plans[0].content==raw["value"]["body"]


@pytest.mark.asyncio
async def test_malformed_draft_json_repairs_transport_without_discarding_body():
    st=state(sources=[source()]);repo=Memory()
    draft="Complete explanation and relationships.\n\nPreserved conclusion."
    malformed=json.dumps(dict(body=draft))[:-1]+",}"
    p=Provider([(malformed,"STOP"),dict(body=draft),conception()[1]])
    await run(Editor(repo,p,Ingestor([]),Search(source())),repo,p,st)
    raw=next(iter(repo.ops.values()))
    assert raw["attempts"][0]["text"]==malformed
    assert raw["attempts"][1]["mode"]=="repair"
    assert st.internal_plans[0].content==draft
    assert "Preserve substantive content" in p.prompts[1][1]


@pytest.mark.asyncio
async def test_legacy_planning_checkpoint_is_retained_but_assessments_are_not_resumed():
    st=state(sources=[source()]);repo=Memory();p=Provider(conception())
    checkpoint=dict(action_id="initial",draft=dict(planning="Previously developed argument."),
                    history=[dict(planning="Earlier valid explanation.")],
                    assessments=[dict(sufficient=False)],feedback="Legacy demand for another assessment")
    st.planning_checkpoint=checkpoint.copy()
    await run(Editor(repo,p,Ingestor([]),Search(source())),repo,p,st)
    assert st.planning_checkpoint==checkpoint
    assert "Previously developed argument" in p.prompts[0][0]
    assert "Legacy demand for another assessment" not in p.prompts[0][0]
    assert len(p.prompts)==2


@pytest.mark.asyncio
async def test_legacy_persisted_plan_after_synthesis_failure_becomes_a_real_draft():
    from uuid import uuid5
    from comunicamundo_v8.hefesto.v8.corpus import Builder
    st=state(sources=[source()]);repo=Memory();p=Provider(conception())
    st.corpus=await Builder(p).build(st.sources, "")
    old_id=uuid5(st.execution_id,"planning:"+identity([st.action["id"],st.corpus.manifest_id]))
    legacy=InternalPlanning(planning_id=old_id,version=1,briefing_hash=identity(st.request.brief),
        corpus_manifest_id="old",content="Preserved previous intellectual work.",
        extent=dict(target_words=150,duration_minutes=1))
    st.internal_plans=[legacy];st.action["planning_id"]=str(legacy.planning_id)
    await run(Editor(repo,p,Ingestor([]),Search(source())),repo,p,st)
    assert st.internal_plans[0]==legacy
    assert st.internal_plans[1].content_kind=="intellectual_draft"
    assert st.internal_plans[1].previous_planning_id==legacy.planning_id
    assert st.internal_plans[1].planning_id!=legacy.planning_id
    assert legacy.content in p.prompts[0][0]
    assert st.projects[0].planning_id==st.internal_plans[1].planning_id

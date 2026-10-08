"""Canonical intellectual product contracts: neutral fixtures, no live models."""
from contextlib import asynccontextmanager
import json
from uuid import uuid4
import pytest

from test_v8 import (Memory, Provider, Ingestor, Search, source, state, request,
                     conception, complete_body, support, run)
from comunicamundo_v4.hefesto.v8.contracts import (EditorialFields, Conception,
    EditorialProject, GeneratedWork, Review, Remoque, State, identity)
from comunicamundo_v4.hefesto.v8.engine import Editor
from comunicamundo_v4.hefesto.v8.provider import Calls, Recoverable
from comunicamundo_v4.hefesto.v8.runtime import Backend, public
from comunicamundo_v4.hefesto.v8.editorial import linked_planning, passages


@pytest.mark.asyncio
@pytest.mark.parametrize("purpose,planning",[
    ("Explain a mechanism", "Start with an everyday phenomenon. Explain its mechanism through an analogy, then distinguish a competing explanation. Develop an objection and the relation between its causes and consequences, concluding what the mechanism makes predictable."),
    ("Investigate an argument", "Open with a disputed claim. Reconstruct its premises and inferential steps, consider a counterexample and develop the implications of a qualified interpretation. Resolve the opening question by contrasting two arguments, rather than following documentary order."),
])
async def test_complete_conception_is_persisted_before_three_field_synthesis_and_hidden(purpose,planning):
    st=state(sources=[source()]);st.request.brief.approach=purpose
    repo=Memory();p=Provider(conception(purpose,planning));editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st)
    internal=st.internal_plans[0];pe=st.projects[0]
    assert internal.content==planning and internal.extent.target_words==150
    assert pe.planning_id==internal.planning_id and pe.planning_version==internal.version
    assert internal.briefing_hash==identity(st.request.brief) and pe.corpus_manifest_id==internal.corpus_manifest_id
    assert set(pe.fields.model_dump())=={"central_idea","narrative_development","intended_conclusion"}
    assert planning in p.prompts[1][0] and "ORIGINAL TEXT" not in p.prompts[1][0]
    assert '"target_words": 150' in p.prompts[0][0] or '"target_words":150' in p.prompts[0][0]
    assert "internal_plans" not in public(st) and "review_checkpoint" not in public(st)
    # A completed planning is present in the persisted state before the PE call.
    class Inspect(Provider):
        async def generate(self,prompt,system,output):
            if "COMPLETE PERSISTED INTERNAL PLANNING" in prompt:
                saved=await repo.get(st.execution_id)
                assert saved.internal_plans and not saved.projects
            return await super().generate(prompt,system,output)
    fresh=state(sources=[source()]);st=fresh;repo=Memory()
    probe=Inspect(conception(purpose,planning))
    await run(Editor(repo,probe,Ingestor([]),Search(source())),repo,probe,st)


@pytest.mark.asyncio
async def test_planning_survives_synthesis_failure_without_repeating_conception():
    st=state(sources=[source()]);repo=Memory()
    outputs=conception("One checkpoint.")
    p=Provider([outputs[0],TimeoutError("transport"),outputs[1]])
    editor=Editor(repo,p,Ingestor([]),Search(source()))
    await repo.create(st)
    with pytest.raises(Recoverable):await editor.execute(st,Calls(repo,p,st))
    assert st.internal_plans and not st.projects
    before=st.internal_plans[0].model_dump(mode="json")
    recovered=await repo.get(st.execution_id)
    await editor.execute(recovered,Calls(repo,p,recovered))
    assert recovered.internal_plans[0].model_dump(mode="json")==before
    assert len(recovered.internal_plans)==1 and len(recovered.projects)==1
    assert sum("CURRENT COMPLETE PLANNING" in x[0] for x in p.prompts)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("structural",[False,True])
async def test_intelligent_remoque_versions_full_planning_and_manifests_effect(structural):
    original="Mission remains explanation. Explain the mechanism, connect its consequences and conclude with a qualification."
    revised=("Mission remains explanation. Open with a counterexample, then investigate consequences before reconstructing the mechanism and its qualified conclusion." if structural else
             "Mission remains explanation. Explain the mechanism with a new analogy, connect its consequences and conclude with a qualification.")
    st=state(sources=[source()]);repo=Memory();p=Provider(conception("Mechanism.",original))
    editor=Editor(repo,p,Ingestor([]),Search(source()));await run(editor,repo,p,st)
    old=st.internal_plans[0].model_dump(mode="json")
    message="Reorganize from the counterexample." if structural else "Use a new explanatory analogy."
    r=Remoque(message=message,base_version=1);st.remoque.append(r)
    st.action=dict(kind="revise",id="revision",version=1,remoque_hash=identity(r))
    p.outputs=conception(message,revised)
    await editor.execute(st,Calls(repo,p,st))
    assert st.internal_plans[0].model_dump(mode="json")==old
    assert st.internal_plans[1].previous_planning_id==st.internal_plans[0].planning_id
    assert st.internal_plans[1].remoque_hash==identity(r)
    assert st.projects[-1].planning_id==st.internal_plans[-1].planning_id
    assert st.projects[-1].fields.central_idea==message
    assert original in p.prompts[2][0] and message in p.prompts[2][0]
    assert "Mission remains explanation" in st.internal_plans[-1].content


@pytest.mark.asyncio
async def test_fully_contemplated_remoque_reports_truth_without_fake_versions():
    st=state(sources=[source()]);repo=Memory();p=Provider(conception());editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st);r=Remoque(message="Keep the qualified conclusion",base_version=1)
    st.remoque.append(r);st.action=dict(kind="revise",id="r",remoque_hash=identity(r),version=1)
    p.outputs=[dict(planning=st.internal_plans[0].content,unchanged=True,revision_effect="A conclusão qualificada já integra a concepção vigente.")]
    await editor.execute(st,Calls(repo,p,st))
    assert len(st.projects)==len(st.internal_plans)==1 and st.notice
    assert st.status=="project_proposed" and len(p.prompts)==3


@pytest.mark.asyncio
async def test_one_approval_persists_both_artifacts_dispatches_once_and_writes_full_conception(monkeypatch):
    st=state(sources=[source()]);repo=Memory();p=Provider(conception());editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st);jobs=[]
    @asynccontextmanager
    async def db():yield repo
    monkeypatch.setattr("comunicamundo_v4.hefesto.v8.runtime.repository",db)
    async def dispatch(args):
        saved=await repo.get(st.execution_id)
        assert saved.projects[-1].approved_at==saved.internal_plans[-1].approved_at
        jobs.append(args);return "operation"
    backend=Backend(dispatch)
    response=await backend.action(st.execution_id,"u","approve",1)
    assert response["status"]=="queued" and "internal_plans" not in response
    await backend.action(st.execution_id,"u","approve",1);assert len(jobs)==1
    recovered=await repo.get(st.execution_id);body=complete_body("Reliable general explanation and an original inference.")
    p.outputs=[dict(title="Work",body=body,support=[]),dict(issues=[])]
    await editor.execute(recovered,Calls(repo,p,recovered))
    assert recovered.status=="text_ready"
    assert recovered.internal_plans[-1].content in p.prompts[-2][0]
    assert recovered.projects[-1].fields.central_idea in p.prompts[-2][0]
    assert "150 words" in p.prompts[-2][0]
    await backend.action(st.execution_id,"u","approve",1);assert len(jobs)==1
    await editor.execute(recovered,Calls(repo,p,recovered));assert len(p.prompts)==4


@pytest.mark.asyncio
async def test_approval_cannot_replace_an_active_revision(monkeypatch):
    st=state(sources=[source()]);repo=Memory();p=Provider(conception())
    await run(Editor(repo,p,Ingestor([]),Search(source())),repo,p,st)
    st.projects[-1].approved_at=st.internal_plans[-1].approved_at="previous approval"
    st.action=dict(kind="revise",id="active-revision",version=1)
    st.status="planning";await repo.save(st)
    @asynccontextmanager
    async def db():yield repo
    monkeypatch.setattr("comunicamundo_v4.hefesto.v8.runtime.repository",db)
    async def dispatch(args):pytest.fail("approval must not replace an active revision")
    with pytest.raises(ValueError,match="awaiting approval"):
        await Backend(dispatch).action(st.execution_id,"u","approve",1)
    assert (await repo.get(st.execution_id)).action["id"]=="active-revision"


@pytest.mark.asyncio
async def test_approval_dispatch_failure_retries_only_scheduling(monkeypatch):
    st=state(sources=[source()]);repo=Memory();p=Provider(conception());await run(Editor(repo,p,Ingestor([]),Search(source())),repo,p,st)
    @asynccontextmanager
    async def db():yield repo
    monkeypatch.setattr("comunicamundo_v4.hefesto.v8.runtime.repository",db)
    jobs=[]
    async def dispatch(args):
        jobs.append(args)
        if len(jobs)==1:raise TimeoutError("dispatch")
        return "accepted"
    backend=Backend(dispatch)
    with pytest.raises(TimeoutError):await backend.action(st.execution_id,"u","approve",1)
    saved=await repo.get(st.execution_id);stamp=saved.internal_plans[-1].approved_at
    assert stamp and saved.projects[-1].approved_at==stamp
    await backend.action(st.execution_id,"u","approve",1)
    await backend.action(st.execution_id,"u","approve",1)
    assert len(jobs)==2 and len(p.prompts)==2
    assert (await repo.get(st.execution_id)).internal_plans[-1].approved_at==stamp


@pytest.mark.asyncio
async def test_work_persisted_before_failed_review_and_not_written_again():
    st=state(sources=[source()]);repo=Memory();body=complete_body()
    p=Provider([*conception(),dict(title="Work",body=body),TimeoutError("verify"),dict(issues=[])])
    editor=Editor(repo,p,Ingestor([]),Search(source()));await run(editor,repo,p,st)
    st.action=dict(kind="approve",id="a",version=1)
    with pytest.raises(Recoverable):await editor.execute(st,Calls(repo,p,st))
    saved=await repo.get(st.execution_id);assert saved.work.body==body and saved.work_versions
    await editor.execute(saved,Calls(repo,p,saved))
    assert saved.status=="text_ready" and len(p.prompts)==5
    assert sum("QUANTITATIVE TARGET" in prompt for prompt,_ in p.prompts)==1


@pytest.mark.asyncio
async def test_nonliteral_documentary_support_general_knowledge_and_inference_are_allowed():
    s=source();st=state(sources=[s]);repo=Memory();body=complete_body("A legitimate interpretation connects two established observations.")
    p=Provider([*conception(),dict(title="Work",body=body,support=[support(s,"Semantic documentary contribution, not a quotation.")]),dict(issues=[])])
    editor=Editor(repo,p,Ingestor([]),Search(s));await run(editor,repo,p,st)
    st.action=dict(kind="approve",id="a",version=1);await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and st.work.support
    assert st.work.support[0].claim not in body
    assert "reliable general knowledge" in p.prompts[-1][1]


@pytest.mark.asyncio
async def test_extent_expansion_develops_planned_ideas_and_keeps_other_parts():
    st=state(sources=[source()]);repo=Memory()
    short="Valid opening.\n\nA mechanism requires explanation.\n\nValid ending."
    expansion=complete_body("Explain the mechanism through its causes, consequences and an objection.")
    p=Provider([*conception(),dict(title="Work",body=short),
        dict(issues=[dict(kind="duration",passage_ids=["p2"],problem="Planned mechanism and objection remain undeveloped.",correction="Develop the planned mechanism, causes and objection to the commissioned extent.")]),
        dict(replacement=expansion),dict(issues=[])])
    editor=Editor(repo,p,Ingestor([]),Search(source()));await run(editor,repo,p,st)
    st.action=dict(kind="approve",id="a",version=1);await editor.execute(st,Calls(repo,p,st))
    assert st.work.body=="Valid opening.\n\n"+expansion+"\n\nValid ending."
    assert len(st.work.body.split())>=150 and st.status=="text_ready"
    assert st.internal_plans[0].content in p.prompts[-2][0]


@pytest.mark.asyncio
async def test_review_repair_has_existing_work_and_planning_without_reconsulting_corpus():
    st=state(sources=[source()]);repo=Memory();p=Provider(conception());editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st)
    st.work=GeneratedWork(title="Work",body=complete_body());st.internal_plans[0].approved_at=st.projects[0].approved_at="now"
    p.outputs=[dict(issues=[dict(kind="factual",passage_ids=["unknown"],problem="A specific fact is uncertain",correction="Qualify the fact")]),dict(issues=[])]
    await Calls(repo,p,st).structured("review-probe",st.corpus.blocks[0],Review,
        repair_context=st.work.body+st.internal_plans[0].content)
    assert st.work.body in p.prompts[-1][0] and st.internal_plans[0].content in p.prompts[-1][0]
    assert st.corpus.blocks[0] not in p.prompts[-1][0]


@pytest.mark.asyncio
async def test_disjoint_corrections_resume_after_failure_without_repeating_done_patch():
    st=state(sources=[source()]);repo=Memory();prefix=complete_body();body=prefix+"\n\nFirst inaccurate attribution.\n\nValid bridge.\n\nSecond unsupported number."
    issues=[dict(kind="factual",passage_ids=["p2"],problem="An attribution is inaccurate",correction="Qualify the attribution"),
            dict(kind="factual",passage_ids=["p4"],problem="A number lacks evidence",correction="Qualify the number")]
    p=Provider([*conception(),dict(title="Work",body=body),dict(issues=issues),dict(replacement="Qualified number."),TimeoutError("patch"),dict(replacement="Qualified attribution."),dict(issues=[])])
    editor=Editor(repo,p,Ingestor([]),Search(source()));await run(editor,repo,p,st)
    st.action=dict(kind="approve",id="a",version=1)
    with pytest.raises(Recoverable):await editor.execute(st,Calls(repo,p,st))
    saved=await repo.get(st.execution_id)
    assert saved.review_checkpoint["next_group"]==1 and "Qualified number." in saved.work.body
    await editor.execute(saved,Calls(repo,p,saved))
    assert saved.status=="text_ready"
    assert saved.work.body==prefix+"\n\nQualified attribution.\n\nValid bridge.\n\nQualified number."
    assert sum("Second unsupported number.\nReturn only" in x[0] for x in p.prompts)==1


def test_old_jsonb_projects_remain_readable_without_fabricating_a_planning():
    st=state();st.projects=[EditorialProject(version=1,document="Preserved historical proposal",corpus_manifest_id="old")]
    payload=st.model_dump(mode="json");payload.pop("internal_plans");payload.pop("review_checkpoint")
    restored=State.model_validate(payload)
    assert restored.projects[0].document=="Preserved historical proposal" and not restored.internal_plans
    with pytest.raises(Recoverable,match="preservada"):linked_planning(restored)


def test_three_field_schema_does_not_create_a_fourth_editorial_field():
    assert set(EditorialFields.model_json_schema()["properties"])=={"central_idea","narrative_development","intended_conclusion"}
    assert [p["passage_id"] for p in passages("Opening.\n\nSecond movement.")]==["p1","p2"]

"""Sized conception and progressive autonomous editing, entirely simulated."""
import json
from copy import deepcopy
from uuid import uuid4
import pytest
from test_v8 import (Memory, Provider, Ingestor, Search, source, state, conception,
                     complete_body, review_ok, problem, confirmed, run)
from comunicamundo_v8.hefesto.v8.engine import Editor
from comunicamundo_v8.hefesto.v8.provider import Calls, Recoverable
from comunicamundo_v8.hefesto.v8.contracts import InternalPlanning
from comunicamundo_v8.hefesto.v8.editorial import has_intellectual_draft
from comunicamundo_v8.hefesto.v8.runtime import public


async def prepared(outputs=(), text="Original corpus exclusively for conception and writing."):
    st=state(sources=[source(text)]);repo=Memory();p=Provider([*conception(),*outputs])
    editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st)
    st.action=dict(kind="approve",id="production",version=1)
    return st,repo,p,editor


def correction(body, strategy="Use a qualified formulation rather than the original assertion.", title="Work"):
    return dict(strategy=strategy, title=title, body=body)


@pytest.mark.asyncio
@pytest.mark.parametrize("approach",["Explain a process","Compare interpretations"])
async def test_draft_precedes_pe_without_sufficiency_or_regeneration(approach):
    st=state(sources=[source()]);st.request.brief.approach=approach;repo=Memory()
    outputs=conception()
    class Inspect(Provider):
        async def generate(self,prompt,system,output):
            if "COMPLETE PERSISTED INTELLECTUAL DRAFT" in prompt:
                saved=await repo.get(st.execution_id)
                assert has_intellectual_draft(saved.internal_plans[-1])
                assert saved.internal_plans[-1].content==outputs[0]["body"]
                assert not saved.projects
            return await super().generate(prompt,system,output)
    p=Inspect(outputs);editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st)
    assert len(p.prompts)==2 and len(st.internal_plans)==1
    assert not st.internal_plans[0].sufficiency and not st.internal_plans[0].movements
    assert not st.planning_checkpoint
    assert st.internal_plans[0].draft_target_words==195
    assert st.internal_plans[0].measured_words==len(outputs[0]["body"].split())
    p.outputs=[dict(title="Work",body=complete_body()),*review_ok()]
    st.action=dict(kind="approve",id="a",version=1)
    await editor.execute(st,Calls(repo,p,st))
    writer,contract=next((prompt,system) for prompt,system in p.prompts if "QUANTITATIVE TARGET" in prompt)
    assert outputs[0]["body"] in writer
    assert "PRIORITY OF WORKING MATERIALS" in contract
    assert "WORKING MATERIAL PRIORITY: 1) intellectual draft" in writer
    assert all("Approve directly when there is no concrete defect" in system for _,system in p.prompts if "STAGE " in system)


@pytest.mark.asyncio
async def test_draft_restart_regenerates_integral_draft_after_truncation_and_timeout():
    st=state(sources=[source()]);repo=Memory();good=conception()
    body=good[0]["body"];raw=json.dumps(good[0]);cut=len(raw)//2
    p=Provider([(raw[:cut],"MAX_TOKENS"),TimeoutError("service"),good[0],good[1]])
    editor=Editor(repo,p,Ingestor([]),Search(source()))
    await repo.create(st)
    with pytest.raises(Recoverable):await editor.execute(st,Calls(repo,p,st))
    saved=await repo.get(st.execution_id)
    journal=next(iter(repo.ops.values()))
    assert journal["attempts"][0]["text"]==raw[:cut] and not saved.projects
    with pytest.raises(Recoverable,match="service"):
        await editor.execute(saved,Calls(repo,p,saved))
    saved=await repo.get(st.execution_id)
    await editor.execute(saved,Calls(repo,p,saved))
    assert saved.status=="project_proposed" and saved.internal_plans[0].content==body
    assert not saved.planning_checkpoint
    assert p.prompts[0]==p.prompts[1]==p.prompts[2]


@pytest.mark.asyncio
async def test_more_than_two_style_corrections_continue_without_reconsider():
    body=complete_body()+"\n\nA minor expressive preference."
    fixed=complete_body()+"\n\nA settled expressive preference."
    diagnosis=dict(problems=[problem(passages=["p2"])])
    st,repo,p,editor=await prepared([dict(title="Work",body=body),*review_ok()[:3],diagnosis,
        correction(body+" first."),confirmed(resolved=False),
        correction(body+" second."),confirmed(resolved=False),
        correction(fixed,"Third integral phrasing that settles the preference."),confirmed(),
    ])
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and all(s.status=="approved" for s in st.editorial_stages)
    stage=st.editorial_stages[3]
    assert len(stage.attempts)==3 and not stage.reconsideration
    assert "settled expressive preference" in st.work.body
    assert all("Two failed interventions" not in system for _,system in p.prompts)


@pytest.mark.asyncio
async def test_consistency_integral_correction_after_failed_attempts():
    body=complete_body().replace("observation153","proven_claim")
    fixed=complete_body().replace("observation153","qualified_claim")
    outputs=[dict(title="Work",body=body),review_ok()[0],dict(problems=[problem(passages=["p1"])]),
        correction(body.replace("proven_claim","certain_claim")),confirmed(resolved=False),
        correction(body.replace("proven_claim","undoubted_claim")),confirmed(resolved=False),
        correction(fixed,"Qualify the causal claim integrally."),confirmed(),*review_ok()[2:]]
    st,repo,p,editor=await prepared(outputs)
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and "qualified_claim" in st.work.body
    assert len(st.editorial_stages[1].attempts)==3
    assert sum("STAGE 1:" in system for _,system in p.prompts)==1
    assert sum("complete corrected work" in system for _,system in p.prompts)==3
    statuses=[s.stage_id for s in st.editorial_stages]
    assert statuses==["completeness","consistency","narrative","style"]
    quality=[(prompt,system) for prompt,system in p.prompts if "STAGE " in system]
    assert all(st.corpus.blocks[0] not in prompt for prompt,_ in quality)
    assert all("approved_planning" not in prompt for prompt,system in quality if "STAGE 4:" in system)


@pytest.mark.asyncio
async def test_consistency_fetches_only_addressed_evidence_for_recorded_doubt():
    text="A specific original observation.\n\nAn unrelated documentary passage must not be loaded."
    st,repo,p,editor=await prepared(text=text)
    ref=dict(source_id=str(st.sources[0].source_id),location_id=st.sources[0].locations[0].location_id)
    p.outputs=[dict(title="Work",body=complete_body()),review_ok()[0],
        dict(evidence_questions=[dict(question="Does the named observation establish the stated fact?",references=[ref])]),
        review_ok()[1],*review_ok()[2:]]
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and len(st.editorial_stages[1].evidence)==1
    quality=[(prompt,system) for prompt,system in p.prompts if "STAGE " in system]
    consultation=[prompt for prompt,system in quality if "STAGE 2:" in system][-1]
    assert "A specific original observation." in consultation
    assert "An unrelated documentary passage" not in consultation
    assert all(text not in prompt for prompt,_ in quality)
    assert all("A specific original observation." not in prompt for prompt,system in quality if "STAGE 2:" not in system)


@pytest.mark.asyncio
async def test_short_writing_is_fixed_by_completeness_not_writing_completion():
    short="Valid opening.\n\nThe planned explanation is technically incomplete.\n\nValid conclusion."
    finished=complete_body("The approved mechanism and objection are now developed.")
    st,repo,p,editor=await prepared([dict(title="Work",body=short),
        dict(problems=[problem(pid="extent",passages=[])]),
        correction(finished,"Realize the commissioned extent integrally."),confirmed(pid="extent"),
        *review_ok()[1:]])
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready"
    assert "writing-completion" not in {v.get("stage") for v in repo.ops.values()}
    assert not st.writing_checkpoint.get("attempts")
    assert st.work.body==finished
    assert "technically incomplete" not in st.work.body


@pytest.mark.asyncio
async def test_confirmation_repairs_new_problem_id_without_reopening_diagnosis():
    body=complete_body()+"\n\nAn imprecise transition."
    fixed=complete_body()+"\n\nA clearer transition."
    st,repo,p,editor=await prepared([dict(title="Work",body=body),*review_ok()[:2],
        dict(problems=[problem(passages=["p2"])]),
        correction(fixed,"Connect the two already consolidated ideas without changing either."),
        confirmed("unexpected_new_problem"),confirmed(),review_ok()[3]])
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and len(st.editorial_stages[2].attempts)==1
    assert sum("Diagnose EXCLUSIVELY this stage ONCE" in system for _,system in p.prompts)==4
    confirmation_prompt=next(prompt for prompt,system in p.prompts if "Confirm ONLY" in system)
    assert "PERTINENT PRIOR VERSION" in confirmation_prompt and "KNOWN PROBLEMS AND RESOLUTION CONDITIONS" in confirmation_prompt
    quality=[system for _,system in p.prompts if "STAGE " in system]
    assert any("LAST authority for structural" in system for system in quality)
    assert any("Preserve ALL consolidated ideas" in system for system in quality)
    assert any("Change linguistic formulation ONLY" in system for system in quality)
    completed=[v for v in repo.ops.values() if v.get("stage")=="narrative-confirm-1"][0]
    assert len(completed["attempts"])==2


@pytest.mark.asyncio
async def test_excessive_objection_can_be_resolved_by_later_integral_correction():
    body=complete_body()+"\n\nThis is explicitly an interpretation, not an established factual claim."
    st,repo,p,editor=await prepared([dict(title="Work",body=body),review_ok()[0],dict(problems=[problem(passages=["p2"])]),
        correction(body),confirmed(resolved=False),
        correction(body),confirmed(resolved=False),
        correction(body,"Keep the explicit interpretation; the objection is not a real defect."),
        confirmed(),*review_ok()[2:]])
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready"
    assert len(st.editorial_stages[1].attempts)==3 and not st.error
    assert "explicitly an interpretation" in st.work.body


def test_legacy_planning_is_preserved_without_being_relabelled_as_a_draft():
    plan=InternalPlanning(planning_id=uuid4(),version=1,briefing_hash="b",corpus_manifest_id="m",content="Topics; one minute.",
        extent=dict(target_words=150,duration_minutes=1))
    assert not has_intellectual_draft(plan)
    assert has_intellectual_draft(plan.model_copy(update=dict(content_kind="intellectual_draft")))


@pytest.mark.asyncio
@pytest.mark.parametrize("failed_save",[1,2])
async def test_final_save_failure_recovers_delivery_without_repeating_approved_operations(failed_save):
    class FailFinalSave(Memory):
        ready_saves=0
        async def save(self,st):
            if st.status=="text_ready":
                self.ready_saves+=1
                if self.ready_saves==failed_save:raise OSError("temporary final checkpoint transport failure")
            await super().save(st)
    st=state(sources=[source()]);repo=FailFinalSave();p=Provider([*conception(),dict(title="Work",body=complete_body()),*review_ok()])
    editor=Editor(repo,p,Ingestor([]),Search(source()));await run(editor,repo,p,st)
    st.action=dict(kind="approve",id="a",version=1)
    with pytest.raises(OSError):await editor.execute(st,Calls(repo,p,st))
    saved=await repo.get(st.execution_id);assert saved.status=="paused"
    approved=[deepcopy(stage.approval) for stage in saved.editorial_stages]
    before=len(p.prompts)
    await editor.execute(saved,Calls(repo,p,saved))
    assert saved.status=="text_ready" and saved.error is None
    assert [stage.approval for stage in saved.editorial_stages]==approved
    assert len(p.prompts)==before and public(saved)["work"]["body"]==complete_body()

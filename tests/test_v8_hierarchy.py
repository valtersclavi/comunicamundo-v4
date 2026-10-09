"""Sized conception and progressive autonomous editing, entirely simulated."""
import json
from copy import deepcopy
from uuid import uuid4
import pytest
from test_v8 import (Memory, Provider, Ingestor, Search, source, state, conception,
                     complete_body, review_ok, problem, confirmed, run)
from comunicamundo_v4.hefesto.v8.engine import Editor
from comunicamundo_v4.hefesto.v8.provider import Calls, Recoverable
from comunicamundo_v4.hefesto.v8.contracts import InternalPlanning, Conception, Sufficiency
from comunicamundo_v4.hefesto.v8.editorial import dimensioned
from comunicamundo_v4.hefesto.v8.runtime import public


async def prepared(outputs=(), text="Original corpus exclusively for conception and writing."):
    st=state(sources=[source(text)]);repo=Memory();p=Provider([*conception(),*outputs])
    editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st)
    st.action=dict(kind="approve",id="production",version=1)
    return st,repo,p,editor


def correction(replacement, strategy="Use a qualified formulation rather than the original assertion."):
    return dict(strategy=strategy,edits=[dict(passage_ids=["p2"],replacement=replacement)])


def decision(disposition, replacement=None):
    return dict(strategy="Reconsider the failed assumptions and settle the known objection responsibly.",
        decisions=[dict(problem_id="p",disposition=disposition,
            reasoning=("The original factual certainty is unwarranted; explicitly qualify it and retain the valid explanation."
                       if replacement else "This minor linguistic preference is not a substantive defect; further edits would impair the established cadence."))],
        edits=[dict(passage_ids=["p2"],replacement=replacement)] if replacement else [],
        preservation_evidence="All other developments, intellectual relationships, narrative order and commissioned extent remain intact.")


@pytest.mark.asyncio
@pytest.mark.parametrize("approach",["Explain a process","Compare interpretations"])
async def test_insufficient_nominal_conception_is_deepened_before_pe(approach):
    st=state(sources=[source()]);st.request.brief.approach=approach;repo=Memory()
    good=conception();nominal=deepcopy(good[0])
    nominal["planning"]="Mention the topics for one minute."
    for m in nominal["movements"]:
        m["development"]="Mention the topic."
        m["space_justification"]="The allocated number is the target."
    insufficient=dict(sufficient=False,reasoning="Numbers match but there is no executable explanation.",
        movement_evidence={},deficiencies=["Develop the mechanism, distinction, objection and their intellectual relationship."])
    class Inspect(Provider):
        async def generate(self,prompt,system,output):
            if "COMPLETE PERSISTED INTERNAL PLANNING" in prompt:
                saved=await repo.get(st.execution_id)
                assert dimensioned(saved.internal_plans[-1]) and len(saved.planning_checkpoint["history"])==1
                assert not saved.projects
            return await super().generate(prompt,system,output)
    p=Inspect([nominal,insufficient,*good]);editor=Editor(repo,p,Ingestor([]),Search(source()))
    await run(editor,repo,p,st)
    assert len(p.prompts)==5 and len(st.internal_plans)==1
    assert good[0]["planning"]==st.internal_plans[0].content
    assert insufficient["deficiencies"][0] in p.prompts[2][0]
    assert "PRESERVED LAST CONCEPTION" in p.prompts[2][0]
    assert set(st.projects[0].fields.model_dump())=={"central_idea","narrative_development","intended_conclusion"}
    p.outputs=[dict(title="Work",body=complete_body()),*review_ok()]
    st.action=dict(kind="approve",id="a",version=1)
    await editor.execute(st,Calls(repo,p,st))
    writer=next(prompt for prompt,_ in p.prompts if "QUANTITATIVE TARGET" in prompt)
    for movement in st.internal_plans[0].movements:
        assert movement.development in writer and movement.space_justification in writer
    assert '"target_words":120' in writer
    assert all("does not complete duration" in system for _,system in p.prompts if "STAGE " in system)


@pytest.mark.asyncio
async def test_planning_insufficiency_progress_survives_transport_restart():
    st=state(sources=[source()]);repo=Memory();good=conception()
    failed=dict(sufficient=False,reasoning="The objection needs substantive reasoning.",movement_evidence={},deficiencies=["Develop the objection."])
    draft=deepcopy(good[0]);draft["planning"]="An incomplete mechanism without its objection."
    p=Provider([draft,failed,TimeoutError("service"),*good]);editor=Editor(repo,p,Ingestor([]),Search(source()))
    await repo.create(st)
    with pytest.raises(Recoverable):await editor.execute(st,Calls(repo,p,st))
    saved=await repo.get(st.execution_id)
    assert saved.planning_checkpoint["history"][0]["planning"]==draft["planning"]
    assert not saved.projects and not saved.internal_plans
    await editor.execute(saved,Calls(repo,p,saved))
    assert saved.status=="project_proposed" and len(saved.planning_checkpoint["assessments"])==2
    assert sum("Assess the COMPLETE conception" in system for _,system in p.prompts)==2


@pytest.mark.asyncio
async def test_two_failed_style_interventions_end_strategy_not_production():
    body=complete_body()+"\n\nA minor expressive preference."
    diagnosis=dict(problems=[problem(passages=["p2"])])
    st,repo,p,editor=await prepared([dict(title="Work",body=body),*review_ok()[:3],diagnosis,
        correction("Another minor expressive preference.","First phrasing adjustment."),confirmed(resolved=False),
        correction("A further minor expressive preference.","Second phrasing with a different cadence."),confirmed(resolved=False),decision("accepted_minor")])
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and all(s.status=="approved" for s in st.editorial_stages)
    stage=st.editorial_stages[3]
    assert len(stage.attempts)==2 and stage.reconsideration["decision"]["decisions"][0]["disposition"]=="accepted_minor"
    assert stage.approval["editorial_decision"]==stage.reconsideration["decision"]
    assert sum("STAGE 4:" in system for _,system in p.prompts)==6
    before=deepcopy(st.model_dump(mode="json"));count=len(p.prompts)
    await editor.execute(st,Calls(repo,p,st))
    assert len(p.prompts)==count and st.model_dump(mode="json")==before
    assert "planning_checkpoint" not in public(st) and "attempts" not in public(st)["editorial_stages"][3]


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupt",[False,True])
async def test_grave_fact_reconsidered_responsibly_and_prior_approvals_preserved(interrupt):
    body=complete_body()+"\n\nThe uncertain cause is conclusively proven."
    outputs=[dict(title="Work",body=body),review_ok()[0],dict(problems=[problem(passages=["p2"])]),
        correction("The cause is certainly demonstrated.","Reword the original certainty."),confirmed(resolved=False),
        correction("The cause is undoubtedly established.","Try emphasizing the proposed relation."),confirmed(resolved=False)]
    if interrupt:outputs.append(TimeoutError("reconsideration service"))
    outputs.extend([decision("qualified","The proposed cause remains uncertain; this is an interpretation, not established causality."),*review_ok()[2:]])
    st,repo,p,editor=await prepared(outputs)
    if interrupt:
        with pytest.raises(Recoverable):await editor.execute(st,Calls(repo,p,st))
        st=await repo.get(st.execution_id);assert st.editorial_stages[1].status=="reconsidering"
        assert public(st)["work"] is None and public(st)["work_versions"]==[]
        approved=deepcopy(st.editorial_stages[0].approval)
        await editor.execute(st,Calls(repo,p,st))
        assert st.editorial_stages[0].approval==approved
    else:await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and "not established causality" in st.work.body
    assert len(st.editorial_stages[1].attempts)==2
    assert sum("STAGE 1:" in system for _,system in p.prompts)==1
    assert sum("Return strategy and one coordinated set of edits" in system for _,system in p.prompts)==2
    statuses=[s.stage_id for s in st.editorial_stages]
    assert statuses==["completeness","consistency","narrative","style"]
    quality=[(prompt,system) for prompt,system in p.prompts if "STAGE " in system]
    assert all(st.corpus.blocks[0] not in prompt for prompt,_ in quality)
    reconsider=next(prompt for prompt,system in quality if "Two failed interventions" in system)
    assert "FAILED INTERVENTIONS AND EXPLANATIONS" in reconsider and "certainly demonstrated" in reconsider
    assert "APPROVED PLANNING AND PE" in reconsider


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
    assert all("approved_planning" not in prompt for prompt,system in quality if "STAGE 4:" in system)


@pytest.mark.asyncio
async def test_writer_reconsiders_no_progress_without_reviewing_partial_work():
    short="Valid opening.\n\nThe planned explanation is technically incomplete.\n\nValid conclusion."
    finished=complete_body("The approved mechanism and objection are now developed.")
    st,repo,p,editor=await prepared([dict(title="Work",body=short),
        correction("The planned explanation is technically incomplete.","An ineffective expression adjustment."),
        correction(finished,"Realize the already planned explanation and objection, retaining the valid ends."),*review_ok()])
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and len(st.writing_checkpoint["attempts"])==2
    assert st.work.body.startswith("Valid opening.") and st.work.body.endswith("Valid conclusion.")
    first_review=next(prompt for prompt,system in p.prompts if "STAGE 1:" in system)
    assert finished in first_review and "technically incomplete" not in first_review
    completions=[prompt for prompt,_ in p.prompts if "ACTUAL WORDS:" in prompt]
    assert "ineffective expression adjustment" in completions[1]


@pytest.mark.asyncio
async def test_reconsideration_cannot_accept_grave_fact_as_minor_or_only_declare_fix():
    body=complete_body()+"\n\nAn uncertain fact is asserted without qualification."
    st,repo,p,editor=await prepared([dict(title="Work",body=body),review_ok()[0],dict(problems=[problem(passages=["p2"])]),
        correction("An uncertain fact is asserted with certainty."),confirmed(resolved=False),
        correction("An uncertain fact remains asserted with certainty."),confirmed(resolved=False),
        decision("accepted_minor"),decision("qualified"),
        decision("qualified","The specific fact is uncertain and must be treated as a qualified possibility."),*review_ok()[2:]])
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready"
    raw=[v for v in repo.ops.values() if v.get("stage")=="consistency-reconsider"][0]
    assert len(raw["attempts"])==3
    assert all(a["raw"] for a in raw["attempts"])
    assert "qualified possibility" in st.work.body


@pytest.mark.asyncio
async def test_confirmation_repairs_new_problem_id_without_reopening_diagnosis():
    body=complete_body()+"\n\nAn imprecise transition."
    st,repo,p,editor=await prepared([dict(title="Work",body=body),*review_ok()[:2],
        dict(problems=[problem(passages=["p2"])]),
        correction("A clearer transition.","Connect the two already consolidated ideas without changing either."),
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
async def test_reconsideration_can_reject_excessive_objection_without_fabricating_edits():
    body=complete_body()+"\n\nThis is explicitly an interpretation, not an established factual claim."
    closure=decision("not_a_defect")
    closure["decisions"][0]["reasoning"]="The actual text explicitly distinguishes interpretation from established fact; the verifier's demand for a literal documentary sentence is excessive."
    st,repo,p,editor=await prepared([dict(title="Work",body=body),review_ok()[0],dict(problems=[problem(passages=["p2"])]),
        correction("This remains explicitly an interpretation, not an established factual claim."),confirmed(resolved=False),
        correction("The passage explicitly offers an interpretation, not an established factual claim."),confirmed(resolved=False),closure,*review_ok()[2:]])
    await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready"
    assert st.editorial_stages[1].approval["editorial_decision"]["decisions"][0]["disposition"]=="not_a_defect"
    assert len(st.editorial_stages[1].attempts)==2 and not st.error


def test_numeric_declaration_without_substantive_evidence_is_not_dimensioned():
    c,s,_=conception()
    plan=InternalPlanning(planning_id=uuid4(),version=1,briefing_hash="b",corpus_manifest_id="m",content="Topics; one minute.",
        extent=dict(target_words=150,duration_minutes=1),movements=Conception.model_validate(c).movements,
        sufficiency=Sufficiency(sufficient=True,reasoning="150 words",movement_evidence={}))
    assert not dimensioned(plan)
    assert not dimensioned(plan.model_copy(update=dict(movements=[])))

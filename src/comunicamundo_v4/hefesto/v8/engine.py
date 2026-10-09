from datetime import UTC, datetime
import json
import re
from .contracts import (EditorialProject, GeneratedWork, Reading, Proposal,
                        SupportAssessment, Verification, Patch, identity)
from .corpus import Builder, originals, validate_references
from .provider import CapacityError, Recoverable

from uuid import uuid5
from .contracts import (InternalPlanning, EditorialFields, Conception, Review, Sufficiency)
from .editorial import (INTELLECTUAL, CONCEIVE, SYNTHESIZE, WRITE, DIMENSION,
                        extent_for, linked_planning, dimensioned, within_extent)

EDITOR = INTELLECTUAL
WRITER = WRITE


class Editor:
    def __init__(self, repository, provider, ingestor, hermes):
        self.repository,self.provider,self.ingestor,self.hermes=repository,provider,ingestor,hermes

    async def checkpoint(self,state,status=None):
        if status: state.status=status
        await self.repository.save(state)

    def direction(self,state):
        return json.dumps(dict(brief=state.request.brief.model_dump(mode="json"),
            remoque=[r.model_dump(mode="json") for r in state.remoque]),ensure_ascii=False)

    async def context(self,state,calls,purpose):
        direction=self.direction(state)
        pe=state.projects[-1].model_dump_json() if state.projects else ""
        planning=state.internal_plans[-1].model_dump_json() if state.internal_plans else ""
        # Include schemas and all operation instructions in the envelope used to
        # route the corpus. Each eventual call also performs exact preflight.
        schemas="\n".join(json.dumps(c.model_json_schema(),ensure_ascii=False)
                           for c in [Conception,EditorialFields,Reading,GeneratedWork,Sufficiency,Patch])
        envelope=CONCEIVE+SYNTHESIZE+WRITE+DIMENSION+schemas+direction+pe+planning+purpose
        corpus=await Builder(self.provider).build(state.sources,envelope)
        if state.corpus != corpus:
            state.corpus=corpus
            await self.checkpoint(state)
        if len(corpus.blocks)==1:
            return corpus.blocks[0]
        readings=[]
        for i,block in enumerate(corpus.blocks):
            reading=await calls.structured("reading-"+identity([identity(block),direction]),
                block+"\nBRIEFING AND EDITORIAL DIRECTION:\n"+direction+
                "\nProduce a grounded editorial reading of this block: relevant ideas/facts, relationships, tensions, contradictions, nuances and indispensable elements. Not a proportional summary. Preserve original references.",
                Reading,corpus.source_ids,EDITOR)
            validate_references(reading.references,state.sources)
            readings.append(reading)
        refs=[r for v in readings for r in v.references]
        # Both grounded readings and every referenced original passage are
        # available to writer/verifier; no old chunk selection or silent cap.
        result="GROUNDED READINGS:\n"+json.dumps([r.model_dump(mode="json") for r in readings],ensure_ascii=False)+"\nORIGINAL SUPPORT:\n"+originals(refs,state.sources)
        cap=await self.provider.capacity()
        if await self.provider.count(result+envelope)+cap.output_tokens>cap.input_tokens:
            raise CapacityError("two-block grounded readings and original support exceed the final context; divide the project")
        return result

    async def execute(self,state,calls):
        action=state.action
        if action.get("id") in state.completed_actions:
            return
        kind=action["kind"]
        if action.get("result"):
            state.completed_actions.append(action["id"])
            await self.checkpoint(state)
            return
        try:
            if kind in {"plan","revise"}:
                await self.plan(state,calls)
            elif kind in {"approve","produce"}:
                if not state.projects or state.projects[-1].version!=action["version"]:
                    raise ValueError("approval refers to a stale PE version")
                pe=state.projects[-1];planning=linked_planning(state,pe)
                if kind=="produce" and (not pe.approved_at or not planning.approved_at):
                    raise ValueError("writing requires explicit approval of the current planning/PE")
                stamp=pe.approved_at or datetime.now(UTC).isoformat()
                pe.approved_at=planning.approved_at=stamp
                await self.checkpoint(state,"writing")
                await self.produce(state,calls)
            else:
                raise ValueError("unsupported V8 action")
            if state.status not in {"paused","no_reliable_support","project_too_large"}:
                state.completed_actions.append(action["id"])
                state.error=None
                await self.checkpoint(state)
        except CapacityError as error:
            state.error=dict(type=type(error).__name__,message=str(error),recoverable=False)
            await self.checkpoint(state,"project_too_large")
        except Exception as error:
            state.error=dict(type=type(error).__name__,message=str(error),recoverable=True,requires_retry_ack=getattr(error,"requires_retry_ack",False))
            await self.checkpoint(state,"paused")
            raise

    async def plan(self,state,calls):
        kind=state.action["kind"]
        if not state.sources:
            if state.request.sources:
                await self.checkpoint(state,"ingesting")
                for source_input in state.request.sources:
                    key=identity(source_input)
                    if key not in state.ingestion:
                        source=await self.ingestor.extract(state,source_input)
                        state.ingestion[key]=source.model_dump(mode="json")
                        if not any(s.source_id==source.source_id for s in state.sources):
                            state.sources.append(source)
                        await self.checkpoint(state)
            else:
                await self.checkpoint(state,"searching_sources")
                ok=await self.hermes.research(state,calls,self.direction(state))
                if not ok:
                    await self.checkpoint(state,"no_reliable_support");return
        # A restart after the first source must still ingest the remaining ones.
        for source_input in state.request.sources:
            key=identity(source_input)
            if key not in state.ingestion:
                await self.checkpoint(state,"ingesting")
                source=await self.ingestor.extract(state,source_input)
                state.ingestion[key]=source.model_dump(mode="json")
                if not any(s.source_id==source.source_id for s in state.sources):
                    state.sources.append(source)
                await self.checkpoint(state)
        await self.checkpoint(state,"planning")
        extent=extent_for(state.request.brief)
        cap=await self.provider.capacity()
        if extent.target_words*4>cap.output_tokens:
            raise CapacityError("requested extent exceeds continuous model output; divide the project")
        current_pe=state.projects[-1] if state.projects else None
        current_plan=None
        if current_pe and current_pe.planning_id:
            current_plan=linked_planning(state,current_pe)
        if kind=="revise" and not any(identity(r)==state.action["remoque_hash"] for r in state.remoque):
            raise ValueError("missing persisted remoque")
        # A saved planning is reused after a crash or interruption of PE synthesis.
        plan=next((p for p in state.internal_plans if str(p.planning_id)==state.action.get("planning_id")),None)
        if plan is None:
            cp=state.planning_checkpoint
            if cp.get("action_id")!=state.action["id"]:
                cp=dict(action_id=state.action["id"],draft=None,history=[],assessments=[],feedback="")
                state.planning_checkpoint=cp
            while True:
                context=await self.context(state,calls,"Complete intellectual conception and quantitative extent")
                prompt=context+"\nCOMMISSION:\n"+self.direction(state)+"\nEXTENT:\n"+extent.model_dump_json()
                prompt+="\nCURRENT COMPLETE PLANNING:\n"+(current_plan.model_dump_json() if current_plan else "none; conceive fully before the PE")
                prompt+="\nCURRENT PE:\n"+(current_pe.model_dump_json() if current_pe else "none")
                prompt+="\nREVISION INTENT:\n"+(state.remoque[-1].message if kind=="revise" else "Initial complete conception")
                prompt+="\nPLANNER DEFICIENCIES TO RESOLVE BEFORE PE:\n"+cp["feedback"]
                if cp["history"]:
                    prompt+="\nPRESERVED LAST CONCEPTION:\n"+json.dumps(cp["history"][-1],ensure_ascii=False)
                    prompt+=f"\nConception development iteration {len(cp['history'])+1}: reconsider the failed approach intellectually, retain valid development and materially deepen the explanatory/argumentative/narrative substance. A repeated nominal conception is not completion. This remains the planner's responsibility, never the writer's or reviewer's."

                if cp["draft"] is not None and cp.get("draft_manifest")!=state.corpus.manifest_id:
                    cp["history"].append(cp["draft"]);cp["draft"]=None
                    await self.checkpoint(state)
                if cp["draft"] is None:
                    conception=await calls.structured("conception",prompt,Conception,
                        [s.source_id for s in state.sources],CONCEIVE)
                    cp["draft"]=conception.model_dump(mode="json");cp["draft_manifest"]=state.corpus.manifest_id
                    await self.checkpoint(state)
                else:
                    conception=Conception.model_validate(cp["draft"])
                if conception.unchanged:
                    if kind!="revise" or current_plan is None or not dimensioned(current_plan):
                        cp["history"].append(cp["draft"]);cp["draft"]=None
                        cp["feedback"]="Unchanged cannot conclude an incomplete conception. Complete substantive development and dimensioned movements before any PE, preserving valid previous decisions."
                        await self.checkpoint(state)
                        continue
                    state.notice=conception.revision_effect or "O remoque já está integralmente contemplado na concepção vigente."
                    state.action["result"]=dict(version=current_pe.version,unchanged=True)
                    await self.checkpoint(state,state.action.get("base_status","project_proposed"));return
                if not conception.research_direction:
                    assessment=await calls.structured("planning-sufficiency",
                        "COMMISSION:\n"+self.direction(state)+"\nEXTENT:\n"+extent.model_dump_json()+
                        "\nCOMPLETE CONCEPTION AND DISTRIBUTION:\n"+conception.model_dump_json(),
                        Sufficiency,system=DIMENSION)
                    candidate=InternalPlanning(planning_id=uuid5(state.execution_id,"draft"),
                        version=1,briefing_hash=identity(state.request.brief),
                        corpus_manifest_id=state.corpus.manifest_id,content=conception.planning,
                        extent=extent,movements=conception.movements,sufficiency=assessment)
                    cp["assessments"].append(assessment.model_dump(mode="json"))
                    if dimensioned(candidate):break
                    cp["history"].append(cp["draft"])
                    cp["feedback"]=assessment.model_dump_json()+"\nAlso ensure unique movements, substantive development/space justification, approximate total matching commissioned extent and concrete sufficiency evidence for every movement. Complete conception itself, not merely numbers."
                    cp["draft"]=None
                    await self.checkpoint(state)
                    continue
                manifest=state.corpus.manifest_id
                await self.checkpoint(state,"searching_sources")
                ok=await self.hermes.research(state,calls,self.direction(state)+"\nINDISPENSABLE GAP:\n"+conception.research_direction)
                if not ok:
                    await self.checkpoint(state,"no_reliable_support");return
                cp["history"].append(cp["draft"]);cp["draft"]=None
                await self.checkpoint(state,"planning")
                await self.context(state,calls,"Reconsider conception after indispensable enrichment")
                if state.corpus.manifest_id==manifest:
                    raise Recoverable("research added no new evidence; conception and corpus preserved")
            plan=InternalPlanning(planning_id=uuid5(state.execution_id,"planning:"+identity([state.action["id"],state.corpus.manifest_id])),
                version=len(state.internal_plans)+1,briefing_hash=identity(state.request.brief),
                corpus_manifest_id=state.corpus.manifest_id,content=conception.planning,extent=extent,movements=conception.movements,sufficiency=assessment,
                previous_planning_id=current_plan.planning_id if current_plan else None,
                remoque_hash=state.action.get("remoque_hash"),revision_effect=conception.revision_effect)
            state.internal_plans.append(plan);state.action["planning_id"]=str(plan.planning_id)
            await self.checkpoint(state)  # Complete conception persisted BEFORE PE.
        fields=await calls.structured("editorial-project",
            "COMMISSION:\n"+self.direction(state)+"\nCOMPLETE PERSISTED INTERNAL PLANNING:\n"+plan.model_dump_json(),
            EditorialFields,system=SYNTHESIZE)
        state.projects.append(EditorialProject(version=len(state.projects)+1,fields=fields,
            planning_id=plan.planning_id,planning_version=plan.version,
            corpus_manifest_id=plan.corpus_manifest_id))
        if state.work and not any(identity(w)==identity(state.work) for w in state.work_versions):
            state.work_versions.append(state.work.model_copy(deep=True))
        state.work=None;state.review_checkpoint={};state.writing_checkpoint={};state.editorial_stages=[];state.notice=None
        state.action["result"]=dict(version=state.projects[-1].version)
        await self.checkpoint(state,"project_proposed")

    def validate_work(self,work,state):
        for support in work.support:
            validate_references(support.references,state.sources)

    async def produce(self,state,calls):
        pe=state.projects[-1];planning=linked_planning(state,pe)
        if not pe.approved_at or not planning.approved_at:
            raise ValueError("writing requires explicit approval of the linked planning/PE")
        if state.action["version"]!=pe.version:
            raise ValueError("writing refers to a stale approved PE")
        if not dimensioned(planning):
            raise Recoverable("approved planning lacks consolidated intellectual dimensioning; preserved PE requires remoque before writing")
        extent=planning.extent
        cap=await self.provider.capacity()
        if extent.target_words*4>cap.output_tokens:
            raise CapacityError("requested extent exceeds continuous model output; divide the project")
        common="\nBRIEFING:\n"+self.direction(state)+"\nAPPROVED COMPLETE INTERNAL PLANNING:\n"+planning.model_dump_json()+"\nAPPROVED PE:\n"+pe.fields.model_dump_json()
        if state.work is None:
            await self.checkpoint(state,"writing")
            context=await self.context(state,calls,"Writing with full planning; target "+str(extent.target_words)+" words")
            work=await calls.structured("write",context+common+
                f"\nQUANTITATIVE TARGET: {extent.target_words} words; {extent.duration_minutes} minutes at {extent.words_per_minute} words/minute. Develop the complete approved conception and ending.",
                GeneratedWork,[s.source_id for s in state.sources],WRITE)
            self.validate_work(work,state)
            state.work=work;state.work_versions.append(work.model_copy(deep=True))
            await self.checkpoint(state)  # Never repeat valid writing after review failure.
        # Completion belongs to writing, never to quality verification.
        from .contracts import Correction
        from .editorial import apply_edits, work_context
        while not within_extent(state.work,extent):
            await self.checkpoint(state,"writing")
            context=await self.context(state,calls,"Complete faithful writing execution before quality verification")
            completion=await calls.structured("writing-completion",context+common+
                "\nPREVIOUS EXECUTION STRATEGIES AND RESULTS:\n"+json.dumps(state.writing_checkpoint.get("attempts",[]),ensure_ascii=False)+
                "\nPRESERVED WRITING:\n"+work_context(state.work,extent)+
                f"\nACTUAL WORDS: {len(state.work.body.split())}; target {extent.target_words}, acceptable ±10%. Finish omitted realization of already planned development; if overlong, condense expression without losing planned substance. Preserve valid text. Do not invent development to compensate planning. Reconsider failed execution strategies and their causes; choose a materially different faithful realization rather than repeating an ineffective intervention. Return coordinated edits and execution strategy.",
                Correction,[s.source_id for s in state.sources],WRITE)
            before=state.work.model_copy(deep=True)
            candidate=apply_edits(before,completion)
            state.writing_checkpoint.setdefault("attempts",[]).append(dict(before=before.model_dump(mode="json"),completion=completion.model_dump(mode="json"),candidate=candidate.model_dump(mode="json")))
            if candidate.body==before.body:
                await self.checkpoint(state)
                continue  # Reconsider execution with preserved failure; never transfer duration to quality review.
            state.work_versions.append(before);state.work=candidate
            await self.checkpoint(state)
        state.writing_checkpoint["complete"]=True
        await self.checkpoint(state,"verifying")
        from .hierarchy import Hierarchy
        if await Hierarchy(self).run(state,calls,planning,pe):
            state.action["result"]=dict(work_hash=identity(state.work),version=pe.version,
                planning_id=str(planning.planning_id))
            await self.checkpoint(state,"text_ready")

from datetime import UTC, datetime
import json
import re
from .contracts import (EditorialProject, GeneratedWork, Reading, Proposal,
                        SupportAssessment, Verification, Patch, identity)
from .corpus import Builder, originals, validate_references
from .provider import CapacityError, Recoverable

from uuid import uuid5
from .contracts import (InternalPlanning, EditorialFields, Conception, Review)
from .editorial import (INTELLECTUAL, CONCEIVE, SYNTHESIZE, WRITE, REVIEW,
                        extent_for, linked_planning, passages, work_context, correction_groups)

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
        planning=state.internal_plans[-1].content if state.internal_plans else ""
        # Include schemas and all operation instructions in the envelope used to
        # route the corpus. Each eventual call also performs exact preflight.
        schemas="\n".join(json.dumps(c.model_json_schema(),ensure_ascii=False)
                           for c in [Conception,EditorialFields,Reading,GeneratedWork,Review,Patch])
        envelope=CONCEIVE+SYNTHESIZE+WRITE+REVIEW+schemas+direction+pe+planning+purpose
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
            while True:
                context=await self.context(state,calls,"Complete intellectual conception and quantitative extent")
                prompt=context+"\nCOMMISSION:\n"+self.direction(state)+"\nEXTENT:\n"+extent.model_dump_json()
                prompt+="\nCURRENT COMPLETE PLANNING:\n"+(current_plan.content if current_plan else "none; conceive fully before the PE")
                prompt+="\nCURRENT PE:\n"+(current_pe.model_dump_json() if current_pe else "none")
                prompt+="\nREVISION INTENT:\n"+(state.remoque[-1].message if kind=="revise" else "Initial complete conception")
                conception=await calls.structured("conception",prompt,Conception,
                    [s.source_id for s in state.sources],CONCEIVE)
                if conception.unchanged:
                    if kind!="revise" or current_plan is None:
                        raise Recoverable("unchanged requires a complete existing planning")
                    state.notice=conception.revision_effect or "O remoque já está integralmente contemplado na concepção vigente."
                    state.action["result"]=dict(version=current_pe.version,unchanged=True)
                    await self.checkpoint(state,state.action.get("base_status","project_proposed"));return
                if not conception.research_direction:break
                manifest=state.corpus.manifest_id
                await self.checkpoint(state,"searching_sources")
                ok=await self.hermes.research(state,calls,self.direction(state)+"\nINDISPENSABLE GAP:\n"+conception.research_direction)
                if not ok:
                    await self.checkpoint(state,"no_reliable_support");return
                await self.checkpoint(state,"planning")
                await self.context(state,calls,"Reconsider conception after indispensable enrichment")
                if state.corpus.manifest_id==manifest:
                    raise Recoverable("research added no new evidence; conception and corpus preserved")
            plan=InternalPlanning(planning_id=uuid5(state.execution_id,"planning:"+identity([state.action["id"],state.corpus.manifest_id])),
                version=len(state.internal_plans)+1,briefing_hash=identity(state.request.brief),
                corpus_manifest_id=state.corpus.manifest_id,content=conception.planning,extent=extent,
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
        state.work=None;state.review_checkpoint={};state.notice=None
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
        await self.checkpoint(state,"verifying")
        while True:
            checkpoint=state.review_checkpoint
            if not checkpoint:
                context=await self.context(state,calls,"Proportional intellectual verification")
                existing=work_context(state.work,extent)
                review=await calls.structured("verify",context+common+"\nEXISTING COMPLETE WORK:\n"+existing,
                    Review,[s.source_id for s in state.sources],REVIEW,
                    repair_context=common+"\nEXISTING COMPLETE WORK:\n"+existing)
                state.verifications.append(Verification(issues=review.issues))
                if not review.issues:
                    state.action["result"]=dict(work_hash=identity(state.work),version=pe.version,
                        planning_id=str(planning.planning_id))
                    await self.checkpoint(state,"text_ready");return
                checkpoint=dict(base_work=state.work.model_dump(mode="json"),
                    groups=correction_groups(review,state.work.body),next_group=0)
                state.review_checkpoint=checkpoint
                await self.checkpoint(state)
            while checkpoint["next_group"]<len(checkpoint["groups"]):
                group=checkpoint["groups"][checkpoint["next_group"]]
                for issue in group["issues"]:
                    if issue.get("research_direction"):
                        await self.checkpoint(state,"searching_sources")
                        ok=await self.hermes.research(state,calls,self.direction(state)+"\nSPECIFIC FACTUAL GAP:\n"+issue["research_direction"])
                        if not ok:
                            await self.checkpoint(state,"no_reliable_support");return
                        await self.checkpoint(state,"verifying")
                context=await self.context(state,calls,"Proportional correction with approved planning")
                start,end=group["start"],group["end"]
                selected=state.work.body[start:end]
                patch=await calls.structured("localized-correction",context+common+
                    "\nEXISTING COMPLETE WORK:\n"+work_context(state.work,extent)+
                    "\nACTIONABLE ISSUES:\n"+json.dumps(group["issues"],ensure_ascii=False)+
                    "\nSELECTED RANGE TO REPLACE:\n"+selected+
                    "\nReturn only its replacement and optional specific documentary support. Preserve every valid part, all text outside this range and approved intellectual decisions. Expand absent planned development when extent is insufficient; no padding or gratuitous rewrite.",
                    Patch,[s.source_id for s in state.sources],INTELLECTUAL,
                    repair_context=common+"\nEXISTING WORK:\n"+state.work.body+"\nSELECTED RANGE:\n"+selected)
                self.validate_work(GeneratedWork(title=state.work.title,body=patch.replacement or " ",support=patch.support),state)
                before=state.work.model_copy(deep=True)
                state.work.body=state.work.body[:start]+patch.replacement+state.work.body[end:]
                state.work.support=list({identity(x):x for x in [*state.work.support,*patch.support]}.values())
                if not state.work.body.strip():
                    state.work=before;raise Recoverable("correction cannot erase the work; prior version preserved")
                state.work_versions.append(before)
                checkpoint["next_group"]+=1
                await self.checkpoint(state)  # Replacement + progress index are one durable state.
            if state.work.body==checkpoint["base_work"]["body"]:
                raise Recoverable("proportional correction made no progress; work and review preserved")
            state.review_checkpoint={}
            await self.checkpoint(state)

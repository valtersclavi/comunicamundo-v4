from datetime import UTC, datetime
import json
import re
from .contracts import (EditorialProject, GeneratedWork, Reading, Proposal,
                        SupportAssessment, Verification, Patch, identity)
from .corpus import Builder, originals, validate_references
from .provider import CapacityError, Recoverable

from uuid import uuid5
from .contracts import (InternalPlanning, EditorialFields, IntellectualDraft)
from .editorial import (INTELLECTUAL, DRAFT, SYNTHESIZE, WRITE,
                        extent_for, linked_planning, has_intellectual_draft, within_extent)

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
                           for c in [IntellectualDraft,EditorialFields,Reading,GeneratedWork,Patch])
        envelope=DRAFT+SYNTHESIZE+WRITE+schemas+direction+pe+planning+purpose
        corpus=await Builder(self.provider).build(state.sources,envelope)
        if state.corpus != corpus:
            state.corpus=corpus
            await self.checkpoint(state)
        if len(corpus.blocks)==1:
            return corpus.blocks[0]
        readings=[]
        for i,block in enumerate(corpus.blocks):
            cached=block+"\nBRIEFING AND EDITORIAL DIRECTION:\n"+direction
            reading=await calls.structured("reading-"+identity([identity(block),direction]),
                cached+
                "\nProduce a grounded editorial reading of this block: relevant ideas/facts, relationships, tensions, contradictions, nuances and indispensable elements. Not a proportional summary. Preserve original references.",
                Reading,corpus.source_ids,EDITOR,cache_prefix=cached,
                cache_slot=i,cache_kind="source-reading")
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
        kind=action["kind"]
        if action.get("result"):
            if kind in {"approve","produce"}:
                if not state.work or len(state.editorial_stages)!=4 or any(s.status!="approved" for s in state.editorial_stages):
                    raise Recoverable("completed production receipt lacks consolidated hierarchy; all progress preserved")
                target="text_ready"
            else:
                target=action.get("base_status","project_proposed") if action["result"].get("unchanged") else "project_proposed"
            completed=action["id"] in state.completed_actions
            if not completed:state.completed_actions.append(action["id"])
            if not completed or state.status!=target or state.error:
                state.error=None
                await self.checkpoint(state,target)
            return
        if action.get("id") in state.completed_actions:
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
            state.error=dict(type=type(error).__name__,message=str(error),recoverable=True)
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
        draft_target=(extent.target_words*130+99)//100
        # Calls uses the full verified model output capacity and exact input
        # preflight. Do not turn an estimated tokens/word ratio into a content
        # ceiling. Incomplete responses are diagnostic data, not draft fragments.
        current_pe=state.projects[-1] if state.projects else None
        current_plan=None
        if current_pe and current_pe.planning_id:
            current_plan=linked_planning(state,current_pe)
        if kind=="revise" and not any(identity(r)==state.action["remoque_hash"] for r in state.remoque):
            raise ValueError("missing persisted remoque")
        # A saved planning is reused after a crash or interruption of PE synthesis.
        plan=next((p for p in state.internal_plans if str(p.planning_id)==state.action.get("planning_id")),None)
        if plan is not None and not has_intellectual_draft(plan):
            current_plan=plan
            plan=None  # Retain legacy progress; do not synthesize an abstract PE.
        if plan is None:
            context=await self.context(state,calls,"Develop the complete surplus intellectual draft")
            prompt=context+"\nCOMMISSION:\n"+self.direction(state)+"\nFINAL EXTENT:\n"+extent.model_dump_json()
            prompt+="\nCURRENT COMPLETE WORKING MATERIAL:\n"+(current_plan.content if current_plan else "none")
            prompt+="\nCURRENT PE:\n"+(current_pe.fields.model_dump_json() if current_pe and current_pe.fields else "none")
            prompt+="\nREVISION INTENT:\n"+(state.remoque[-1].message if kind=="revise" else "Initial intellectual development")
            # Preserve useful work from an interrupted legacy action, without
            # continuing its former assessments or regeneration loop.
            cp=state.planning_checkpoint
            if cp.get("action_id")==state.action["id"]:
                previous=cp.get("draft") or next(iter(reversed(cp.get("history",[]))),None)
                if previous:
                    prompt+="\nPRESERVED EARLIER WORK:\n"+json.dumps(previous,ensure_ascii=False)
            draft=await calls.structured("intellectual-draft",prompt,IntellectualDraft,
                [s.source_id for s in state.sources],DRAFT.format(
                    palavras_contratadas=extent.target_words,palavras_rascunho=draft_target),
                cache_prefix=prompt)
            if (kind=="revise" and current_plan and has_intellectual_draft(current_plan)
                    and draft.body==current_plan.content):
                state.notice="O remoque já está contemplado no rascunho vigente; o texto foi preservado."
                state.action["result"]=dict(version=current_pe.version,unchanged=True)
                await self.checkpoint(state,state.action.get("base_status","project_proposed"));return
            plan=InternalPlanning(planning_id=uuid5(state.execution_id,"intellectual-draft:"+identity([state.action["id"],state.corpus.manifest_id])),
                version=len(state.internal_plans)+1,briefing_hash=identity(state.request.brief),
                corpus_manifest_id=state.corpus.manifest_id,content=draft.body,extent=extent,
                content_kind="intellectual_draft",draft_target_words=draft_target,
                measured_words=len(draft.body.split()),
                previous_planning_id=current_plan.planning_id if current_plan else None,
                remoque_hash=state.action.get("remoque_hash"))
            state.internal_plans.append(plan);state.action["planning_id"]=str(plan.planning_id)
            state.telemetry.append(dict(kind="artifact",stage="intellectual-draft",
                planning_id=str(plan.planning_id),target_words=draft_target,
                measured_words=plan.measured_words,contracted_words=extent.target_words))
            await self.checkpoint(state)  # Entire draft durable BEFORE PE synthesis.
        synthesis="COMMISSION:\n"+self.direction(state)+"\nCOMPLETE PERSISTED INTELLECTUAL DRAFT:\n"+plan.model_dump_json()
        fields=await calls.structured("editorial-project",synthesis,
            EditorialFields,system=SYNTHESIZE,cache_prefix=synthesis)
        state.projects.append(EditorialProject(version=len(state.projects)+1,fields=fields,
            planning_id=plan.planning_id,planning_version=plan.version,
            corpus_manifest_id=plan.corpus_manifest_id))
        if state.work and not any(identity(w)==identity(state.work) for w in state.work_versions):
            state.work_versions.append(state.work.model_copy(deep=True))
        state.work=None;state.review_checkpoint={};state.writing_checkpoint={};state.editorial_stages=[];state.notice=None
        state.action["result"]=dict(version=state.projects[-1].version)
        await self.checkpoint(state,"project_proposed")

    def validate_work(self,work,state):
        from .corpus import sanitize_support
        sanitize_support(work,state.sources)
        for support in work.support:
            validate_references(support.references,state.sources)

    async def produce(self,state,calls):
        pe=state.projects[-1];planning=linked_planning(state,pe)
        if not pe.approved_at or not planning.approved_at:
            raise ValueError("writing requires explicit approval of the linked planning/PE")
        if state.action["version"]!=pe.version:
            raise ValueError("writing refers to a stale approved PE")
        if not has_intellectual_draft(planning):
            raise Recoverable("previous planning preserved; remoque must develop the intellectual draft before writing")
        extent=planning.extent
        cap=await self.provider.capacity()
        if extent.target_words*4>cap.output_tokens:
            raise CapacityError("requested extent exceeds continuous model output; divide the project")
        common="\nBRIEFING:\n"+self.direction(state)+"\nAPPROVED COMPLETE INTELLECTUAL DRAFT (RAW MATERIAL):\n"+planning.model_dump_json()+"\nAPPROVED PE:\n"+pe.fields.model_dump_json()
        if state.work is None:
            await self.checkpoint(state,"writing")
            context=await self.context(state,calls,"Writing with full planning; target "+str(extent.target_words)+" words")
            work=await calls.structured("write",context+common+
                f"\nQUANTITATIVE TARGET: {extent.target_words} words; {extent.duration_minutes} minutes at {extent.words_per_minute} words/minute. Select, reorganize, condense and rewrite the surplus draft into the commissioned final work and ending.",
                GeneratedWork,[s.source_id for s in state.sources],WRITE,cache_prefix=context+common)
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
                f"\nACTUAL WORDS: {len(state.work.body.split())}; target {extent.target_words}, acceptable ±10%. Finish realization of the approved conception, including deeper relationships and explanations where pertinent; if overlong, condense expression without losing planned substance. Preserve valid text. Do not introduce unrelated themes, invented evidence or padding. Reconsider failed execution strategies and their causes; choose a materially different faithful realization rather than repeating an ineffective intervention. Return coordinated edits and execution strategy.",
                Correction,[s.source_id for s in state.sources],WRITE,cache_prefix=context+common)
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


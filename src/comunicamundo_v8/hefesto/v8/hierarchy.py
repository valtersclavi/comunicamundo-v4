"""Progressive editorial authority with durable diagnoses and integral correction."""
from datetime import UTC, datetime
import json
from .contracts import (EditorialStage, Diagnosis, Correction, Confirmation,
                        GeneratedWork, identity)
from .editorial import STAGES, stage_contract, work_context, apply_correction, within_extent
from .corpus import originals
from .provider import Recoverable


class Hierarchy:
    def __init__(self, editor):self.editor = editor

    def protected(self,state,position):
        return [{k:v for k,v in s.approval.items() if k!="consolidated_work"} for s in state.editorial_stages if s.position < position]

    def cached_context(self,state,planning,pe,stage):
        data=dict(brief=state.request.brief.model_dump(mode='json'))
        if stage.position < 4:
            data.update(approved_planning=planning.model_dump(mode='json'),
                        approved_pe=pe.fields.model_dump(mode='json'))
        return "STABLE EDITORIAL MATERIAL:\n"+json.dumps(data,ensure_ascii=False)

    def context(self,state,planning,pe,stage,work):
        data=dict(work=json.loads(work_context(work,planning.extent)),
                  earlier_approvals=self.protected(state,stage.position))
        if stage.position == 2:
            data['address_catalog']=[dict(source_id=str(s.source_id),title=s.title,
                locations=[l.model_dump(mode='json') for l in s.locations]) for s in state.sources]
            data['specific_documentary_evidence']=stage.evidence
        return self.cached_context(state,planning,pe,stage)+"\nCURRENT EDITORIAL STATE:\n"+json.dumps(data,ensure_ascii=False)

    async def approve(self,state,stage):
        stage.status='approved'
        stage.approval=dict(stage_id=stage.stage_id,position=stage.position,
            approved_at=datetime.now(UTC).isoformat(),work_hash=identity(state.work),
            consolidated_work=state.work.model_dump(mode='json'),
            criteria=STAGES[stage.position-1][2],authority=STAGES[stage.position-1][3],
            confirmations=[a.get('confirmation') for a in stage.attempts])
        await self.editor.checkpoint(state)

    async def run(self,state,calls,planning,pe):
        if not state.editorial_stages:
            state.editorial_stages=[EditorialStage(position=i,stage_id=v[0]) for i,v in enumerate(STAGES,1)]
            await self.editor.checkpoint(state)
        if [s.position for s in state.editorial_stages]!=[1,2,3,4]:
            raise Recoverable('editorial hierarchy checkpoint is inconsistent; all work preserved')
        for stage in state.editorial_stages:
            if stage.status=='approved':continue
            if any(s.status!='approved' for s in state.editorial_stages if s.position<stage.position):
                raise Recoverable('earlier editorial approval is missing')
            if stage.status in {'paused','reconsidering'}:
                # Old reconsider checkpoints resume as ordinary correction.
                stage.status='correcting'
            if stage.diagnosis is None:
                if stage.examined_work is None:stage.examined_work=state.work.model_copy(deep=True)
                stage.status='diagnosing';await self.editor.checkpoint(state)
                while True:
                    prompt=self.context(state,planning,pe,stage,stage.examined_work)
                    if stage.evidence:
                        prompt+='\nResolve ONLY the recorded concrete factual questions with their addressed evidence and complete this same diagnosis; do not start a new general verification.'
                    diagnosis=await calls.structured(f'{stage.stage_id}-diagnose',prompt,Diagnosis,
                        system=stage_contract(stage.position,'diagnose'),repair_context=prompt,
                        cache_prefix=self.cached_context(state,planning,pe,stage))
                    if not diagnosis.evidence_questions:break
                    if stage.position!=2:
                        raise Recoverable('only consistency may request specific documentary evidence')
                    changed=False
                    for question in diagnosis.evidence_questions:
                        key=identity(question)
                        if key not in stage.evidence:
                            stage.evidence[key]=json.dumps(dict(question=question.question,
                                original_passages=originals(question.references,state.sources)),ensure_ascii=False)
                            changed=True
                    await self.editor.checkpoint(state)
                    if not changed:
                        raise Recoverable('specific factual consultation made no progress; questions and original evidence preserved')
                stage.diagnosis=diagnosis
                await self.editor.checkpoint(state)
            if not stage.diagnosis.problems:
                await self.approve(state,stage);continue
            while stage.status!='approved':
                active=stage.attempts[-1] if stage.attempts and 'confirmation' not in stage.attempts[-1] else None
                if active is None:
                    active=dict(number=len(stage.attempts)+1,before=state.work.model_dump(mode='json'))
                    stage.attempts.append(active)
                    stage.status='correcting';await self.editor.checkpoint(state)
                if 'correction' not in active:
                    stage.status='correcting';await self.editor.checkpoint(state)
                    prompt=self.context(state,planning,pe,stage,state.work)
                    prompt+='\nKNOWN DIAGNOSIS:\n'+stage.diagnosis.model_dump_json()
                    prompt+='\nPREVIOUS STRATEGIES AND SPECIFIC RESULTS:\n'+json.dumps(stage.attempts[:-1],ensure_ascii=False)
                    correction=await calls.structured(f'{stage.stage_id}-correct-{active["number"]}',prompt,
                        Correction,system=stage_contract(stage.position,'correct'),repair_context=prompt,
                        cache_prefix=self.cached_context(state,planning,pe,stage))
                    active['correction']=correction.model_dump(mode='json')
                    active['candidate']=apply_correction(state.work,correction).model_dump(mode='json')
                    stage.status='confirming';await self.editor.checkpoint(state)
                stage.status='confirming';await self.editor.checkpoint(state)
                candidate=GeneratedWork.model_validate(active['candidate'])
                prompt=self.context(state,planning,pe,stage,candidate)
                prompt+='\nKNOWN PROBLEMS AND RESOLUTION CONDITIONS:\n'+stage.diagnosis.model_dump_json()
                prompt+='\nPERTINENT PRIOR VERSION:\n'+json.dumps(active['before'],ensure_ascii=False)
                prompt+='\nEXECUTED STRATEGY:\n'+json.dumps(active['correction'],ensure_ascii=False)
                confirmation=await calls.structured(f'{stage.stage_id}-confirm-{active["number"]}',prompt,
                    Confirmation,system=stage_contract(stage.position,'confirm'),repair_context=prompt,
                    cache_prefix=self.cached_context(state,planning,pe,stage))
                # Confirmation and candidate promotion share one durable save.
                active['confirmation']=confirmation.model_dump(mode='json')
                valid=confirmation.constraints_preserved and within_extent(candidate,planning.extent)
                active['valid']=valid
                if not within_extent(candidate,planning.extent):
                    active['preservation_failure']='intervention left the work below or above the commissioned extent band; restore substantive commissioned extent'
                if valid:
                    state.work_versions.append(state.work.model_copy(deep=True));state.work=candidate
                resolved=valid and all(r.resolved for r in confirmation.resolutions)
                if resolved:
                    await self.approve(state,stage)
                else:
                    stage.status='correcting';await self.editor.checkpoint(state)
        return True

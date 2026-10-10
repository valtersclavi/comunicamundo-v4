"""Semantic search policy, separate from Hefesto editorial orchestration."""
import asyncio
import json
from time import perf_counter
from .telemetry import external
from .contracts import SearchDecision, IdentityAssessment, Source, identity
from .corpus import render_range
from .provider import Recoverable


class Hermes:
    def __init__(self, repository, discovery, acquirer, ingestor):
        self.repository,self.discovery,self.acquirer,self.ingestor=repository,discovery,acquirer,ingestor

    async def research(self,state,calls,direction):
        if self.discovery is None:
            raise Recoverable("Hermes requires a configured search provider (YOU_API_KEY or OPENALEX_API_KEY)")
        key=identity(direction)
        research=state.research.setdefault(key,dict(direction=direction,queries={},candidates={},judgments={},accepted=[],complete=False))
        if research.get("complete"):
            return research["result"]=="success"
        while True:
            # Summaries here are operational search receipts, never editorial
            # substitutes for corpus or extraction. Candidate descriptions come
            # from search providers. Full acquired text is read before acceptance.
            summary={k:v for k,v in research.items() if k not in {"acquired","pending_decision"}}
            decision=SearchDecision.model_validate(research["pending_decision"]) if research.get("pending_decision") else await calls.structured("hermes-search-decision",json.dumps(summary,ensure_ascii=False)+
                "\nDIRECTION:\n"+direction+
                "\nFind trustworthy institutional/academic/documentary support for this request. Search multilingual material and formats as appropriate, adapt to results. Output search with new specific queries, success selecting acquired accepted sources, or no_reliable_support with a reason grounded in actual search evidence and adequate investigation of alternatives. Never conclude absence due to attempts/pages/time/rate limits. Do not fabricate support for a false proposition; material discussing it is not evidence for its truth.",
                SearchDecision,system="You are Hermes: source search, selection and acquisition, not an editorial proposal writer.")
            research["pending_decision"]=decision.model_dump(mode="json")
            await self.repository.save(state)
            if decision.decision=="no_reliable_support":
                if not research["queries"] or not all(v.get("complete") for v in research["queries"].values()):
                    raise Recoverable("absence of support was asserted before completed search evidence")
                research.update(complete=True,result="no_reliable_support",reason=decision.reason)
                state.error=dict(type="NoReliableSupport",message=decision.reason,recoverable=False)
                await self.repository.save(state)
                return False
            if decision.decision=="success":
                chosen=decision.selected_ids
                if not chosen or any(k not in research["accepted"] for k in chosen):
                    raise Recoverable("search success requires identified sources with verified acquisition/support")
                for k in chosen:
                    source=Source.model_validate(research["acquired"][k])
                    if not any(s.source_id==source.source_id for s in state.sources):
                        state.sources.append(source)
                research.update(complete=True,result="success",reason=decision.reason)
                await self.repository.save(state)
                return True
            changed=False
            for query in decision.queries:
                qkey=identity(query)
                record=research["queries"].setdefault(qkey,dict(query=query,results={},complete=False))
                if record["complete"]:continue
                # Provider count is a transport page size. Search policy can
                # request further queries; there is no max-round conclusion.
                for provider in self.discovery._providers.values():
                    if provider.name not in record["results"]:
                        started=perf_counter()
                        try:
                            candidates=await asyncio.to_thread(provider.search,query,count=10)
                        except Exception as error:
                            state.telemetry.append(external("hermes_search",provider.name,int((perf_counter()-started)*1000),error=str(error)))
                            await self.repository.save(state)
                            raise
                        state.telemetry.append(external("hermes_search",provider.name,int((perf_counter()-started)*1000)))
                        record["results"][provider.name]=[c.model_dump(mode="json") for c in candidates]
                        for c in candidates:
                            research["candidates"][c.candidate_id]=c.model_dump(mode="json")
                        await self.repository.save(state)
                record["complete"]=True
                changed=True
                await self.repository.save(state)
            for cid in decision.selected_ids:
                if cid in research["judgments"]:continue
                if cid not in research["candidates"]:
                    raise Recoverable("Hermes selected unknown candidate")
                from ..contracts import SourceCandidate
                candidate=SourceCandidate.model_validate(research["candidates"][cid])
                research.setdefault("acquired",{})
                if cid not in research["acquired"]:
                    source=await self.ingestor.acquire(state,candidate,self.acquirer)
                    research["acquired"][cid]=source.model_dump(mode="json")
                    await self.repository.save(state)
                source=Source.model_validate(research["acquired"][cid])
                result=await calls.structured("hermes-source-identity",render_range(source)+
                    "\nEXPECTED DOCUMENT:\n"+candidate.model_dump_json()+"\nDIRECTION:\n"+direction+
                    "\nCheck acquired identity against title/author/publication/DOI, actual document completeness and reliable relevant support for the requested direction. Similar topic is not the same document. A label saying acquired is not proof. Do not invent missing original content. Explain rejection.",
                    IdentityAssessment,[source.source_id],"You verify source identity, completeness and reliable relevance.")
                research["judgments"][cid]=result.model_dump(mode="json")
                if result.same_document and result.complete and result.reliable_support:
                    research["accepted"].append(cid)
                changed=True
                await self.repository.save(state)
            research.pop("pending_decision",None)
            await self.repository.save(state)
            if not changed:
                raise Recoverable("search made no progress; receipts preserved for recovery, not an absence-of-support conclusion")

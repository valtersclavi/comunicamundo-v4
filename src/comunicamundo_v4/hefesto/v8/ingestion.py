import asyncio
from .telemetry import external
from hashlib import sha256
from .contracts import Location, Source
from .provider import Recoverable
from ..contracts import EditorialInputRequest, SourceInput
from ..ingestion import ingest
from ..segmentation import segment_sources


class Ingestor:
    def __init__(self,client,bucket,provider,acquirer=None):
        self.client,self.bucket,self.provider,self.acquirer=client,bucket,provider,acquirer

    def request(self,state,source):
        return EditorialInputRequest(project_id=state.request.project_id,
                                     brief=state.request.brief,sources=[source])

    async def extract(self,state,source_input):
        result=await asyncio.to_thread(ingest,self.request(state,source_input),
            client=self.client,bucket_name=self.bucket,acquirer=self.acquirer)
        return await self.adapt(result,source_input)

    async def acquire(self,state,candidate,acquirer):
        payload=await asyncio.to_thread(acquirer.acquire,candidate)
        for attempt in payload.record.attempts:
            state.telemetry.append(external("hermes_acquire",attempt.route,attempt.duration_ms,
                error=attempt.error))
        if payload.record.candidate.candidate_id!=candidate.candidate_id:
            raise Recoverable("Acquire changed the selected document identity")
        if payload.record.status!="acquired":
            # This is an acquisition/transport pause, not no reliable support.
            state.research.setdefault("acquisition_failures",{})[candidate.candidate_id]=payload.record.model_dump(mode="json")
            raise Recoverable(payload.record.failure_reason or "selected source could not be acquired")
        source_input=SourceInput(uri=candidate.acquisition_url,title=candidate.title,
                                 doi=candidate.doi,publisher=candidate.publisher)
        from ..source_pipeline import SourceAcquirer
        class SelectedPayload(SourceAcquirer):
            def __init__(self):
                pass
            def acquire(self, selected):
                if selected.acquisition_url != candidate.acquisition_url:
                    raise Recoverable("selected acquisition URL changed")
                return payload
        result=await asyncio.to_thread(ingest,self.request(state,source_input),
            client=self.client,bucket_name=self.bucket,acquirer=SelectedPayload())
        return await self.adapt(result,source_input)

    async def adapt(self,result,source_input):
        if result.receipt.failed_acquisitions or result.receipt.failed_normalizations:
            raise Recoverable("source extraction incomplete; original/receipt preserved")
        receipt=result.receipt.sources[0]
        if receipt.normalization_status!="normalized":
            raise Recoverable("partial extraction cannot silently become the complete corpus")
        original=result.request.sources[0]
        paragraphs=segment_sources([original])
        locations=[]
        cursor=0
        for paragraph in paragraphs:
            # Preserve page/section addresses and exact continuous original text.
            start=original.text.find(paragraph.text,cursor)
            if start<0:
                raise Recoverable("extracted paragraph cannot be located in canonical source text")
            end=start+len(paragraph.text)
            locations.append(Location(location_id=paragraph.paragraph_id,start=start,end=end,
                                      page=paragraph.page_no))
            cursor=end
        tokens=await self.provider.count(original.text)
        meta=original.metadata
        return Source(source_id=original.source_id,title=original.title,
            author=meta.get("author"),publisher=meta.get("publisher") or source_input.publisher,
            origin=source_input.uri,doi=meta.get("doi") or source_input.doi,
            original_uri=receipt.original_uri,binary_sha256=receipt.binary_sha256,
            text_sha256=sha256(original.text.encode()).hexdigest(),
            extraction_method=str(meta.get("normalizer") or "existing-normalizer"),
            text=original.text,tokens=tokens,locations=locations,metadata=meta)

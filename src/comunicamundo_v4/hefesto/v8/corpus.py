from dataclasses import asdict
from hashlib import sha256
import json
from .contracts import Corpus, Source, Reference, identity
from .provider import CapacityError


def render_range(source: Source, start=0, end=None) -> str:
    """Entire source text, including gaps, is represented exactly once.

    Addresses decorate text but never replace it with paragraph summaries.
    """
    end = len(source.text) if end is None else end
    addresses=[dict(location_id=l.location_id,start=max(start,l.start),end=min(end,l.end),page=l.page,section=l.section)
               for l in source.locations if l.end>start and l.start<end]
    header=(f"SOURCE {source.source_id} VERSION {source.text_sha256}\n"
        f"IDENTITY {json.dumps(dict(title=source.title,author=source.author,publisher=source.publisher,origin=source.origin,doi=source.doi),ensure_ascii=False)}\n"
        f"LOCATIONS {json.dumps(addresses,ensure_ascii=False)}\n"
        f"ORIGINAL TEXT chars {start}:{end}:\n")
    return header+source.text[start:end]



def validate_references(refs: list[Reference], sources: list[Source]):
    index = {(s.source_id,l.location_id) for s in sources for l in s.locations}
    if any((r.source_id,r.location_id) not in index for r in refs):
        raise ValueError("support references an unknown source/location")


def originals(refs: list[Reference], sources: list[Source]) -> str:
    validate_references(refs,sources)
    wanted = {(r.source_id,r.location_id) for r in refs}
    result = []
    for s in sources:
        for loc in s.locations:
            if (s.source_id,loc.location_id) in wanted:
                result.append(render_range(s,loc.start,loc.end))
    return "\n\n".join(result)


class Builder:
    def __init__(self, provider):
        self.provider=provider

    async def build(self,sources: list[Source],envelope: str) -> Corpus:
        if not sources:
            raise ValueError("Hermes must construct a corpus before editorial planning")
        if len({s.source_id for s in sources}) != len(sources):
            raise ValueError("duplicate corpus source")
        cap=await self.provider.capacity()
        overhead=await self.provider.count(envelope)
        useful=cap.input_tokens-2*cap.output_tokens-overhead
        if useful<=0:
            raise CapacityError("briefing/instructions/output leave no corpus capacity")
        whole="\n\n".join(render_range(s) for s in sources)
        whole_count=await self.provider.count(whole)
        if whole_count<=useful:
            blocks=[whole]
        else:
            # Prefer a complete source boundary. Then paragraph/chapter boundaries
            # within the one source crossed. There is never an editorial LLM call
            # here, nor recursion into a tree of syntheses.
            boundaries=[]
            for i,s in enumerate(sources):
                if i:boundaries.append((i,0))
                boundaries.extend((i,l.end) for l in s.locations[:-1])
            def split(boundary):
                i,cut=boundary
                if cut==0:
                    left="\n\n".join(render_range(s) for s in sources[:i])
                    right="\n\n".join(render_range(s) for s in sources[i:])
                else:
                    left="\n\n".join([*(render_range(s) for s in sources[:i]),render_range(sources[i],0,cut)])
                    right="\n\n".join([render_range(sources[i],cut),*(render_range(s) for s in sources[i+1:])])
                return left,right
            blocks=None
            # Try source boundaries first; no materialized quadratic prefix list.
            for boundary in (v for v in boundaries if v[1]==0):
                left,right=split(boundary)
                if await self.provider.count(left)<=useful and await self.provider.count(right)<=useful:
                    blocks=[left,right];break
            if blocks is None:
                lo,hi=0,len(boundaries)-1;best=None
                while lo<=hi:
                    mid=(lo+hi)//2
                    left,right=split(boundaries[mid])
                    if await self.provider.count(left)<=useful:
                        best=(left,right);lo=mid+1
                    else:hi=mid-1
                if best and await self.provider.count(best[1])<=useful:blocks=list(best)
            # If one paragraph itself is too large, a technical character
            # boundary is unavoidable; retain original location id and offsets.
            if blocks is None:
                lengths=[len(s.text) for s in sources]
                total=sum(lengths)
                def technical_split(position):
                    offset=position
                    for i,length in enumerate(lengths):
                        if offset<=length:
                            if offset==length and i+1<len(sources):return split((i+1,0))
                            return split((i,offset))
                        offset-=length
                    raise ValueError("invalid corpus boundary")
                lo,hi=1,total-1;best=None
                while lo<=hi:
                    mid=(lo+hi)//2;left,right=technical_split(mid)
                    if await self.provider.count(left)<=useful:best=(left,right);lo=mid+1
                    else:hi=mid-1
                if best and await self.provider.count(best[1])<=useful:blocks=list(best)
            if blocks is None:
                raise CapacityError("corpus cannot fit in at most two contexts; divide this project")
        counts=[await self.provider.count(b) for b in blocks]
        versions={str(s.source_id):s.text_sha256 for s in sources}
        budget=dict(asdict(cap), envelope_tokens=overhead,reserved_output_tokens=cap.output_tokens, reserved_next_artifact_tokens=cap.output_tokens,
                    technical_margin_tokens=0,
                    margin_reason="full provider output and next generated artifact (PE/work) reserved; exact operation preflight additionally counts schema/system/history")
        return Corpus(manifest_id=identity(versions),
                      source_ids=[s.source_id for s in sources],source_versions=versions,
                      content_tokens=sum(s.tokens for s in sources),blocks=blocks,
                      block_tokens=counts,useful_tokens=useful,budget=budget)

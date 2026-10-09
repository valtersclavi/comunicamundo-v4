"""Explicit cache lifecycle and SDK transport, entirely offline."""
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from google.genai import types

from test_v8 import Provider, Memory, source, state, request, Ingestor, Search, support, vertex_fixture, conception, complete_body, review_ok, problem, confirmed
from comunicamundo_v4.hefesto.v8.provider import Calls, Recoverable, CapacityError
from comunicamundo_v4.hefesto.v8.context import Contexts
from comunicamundo_v4.hefesto.v8.corpus import Builder
from comunicamundo_v4.hefesto.v8.engine import Editor
from comunicamundo_v4.hefesto.v8.contracts import Proposal, Remoque, identity
from comunicamundo_v4.hefesto.v8.runtime import public


class Missing(Exception):
    code = 404


class CachedProvider(Provider):
    provider = "fixture"
    project = "fixture-project"
    location = "global"

    def __init__(self, outputs=(), **kwargs):
        super().__init__(outputs, **kwargs)
        self.cap = replace(self.cap, cache_min_tokens=2048, cache_inline_max_bytes=10000000,
            cache_ttl_seconds=3600, cache_storage_price="1", cache_source="official-fixture",
            input_price="0.30", cached_price="0.03", output_price="2.50")
        self.resources = {}; self.creations = []; self.renewals = []; self.reads = []
        self.create_error = None

    async def cache_create(self, text, display_name, cap):
        self.creations.append((text, display_name))
        if self.create_error: raise self.create_error
        now = datetime.now(UTC)
        resource = f"projects/p/locations/{self.location}/cachedContents/{len(self.creations)}"
        meta = dict(resource=resource, model=self.model, token_count=await self.count(text),
            created_at=now.isoformat(), expire_at=(now+timedelta(seconds=cap.cache_ttl_seconds)).isoformat())
        self.resources[resource] = dict(meta, display_name=display_name)
        return deepcopy(meta)

    async def cache_get(self, name):
        if name not in self.resources: raise Missing()
        return deepcopy(self.resources[name])

    async def cache_renew(self, name, ttl):
        self.renewals.append((name, ttl))
        self.resources[name]["expire_at"] = (datetime.now(UTC)+timedelta(seconds=ttl)).isoformat()
        return deepcopy(self.resources[name])

    async def cache_find(self, display):
        return next((deepcopy(v) for v in self.resources.values() if v["display_name"]==display
                     and datetime.fromisoformat(v["expire_at"])>datetime.now(UTC)), None)

    async def cache_delete(self, resource):
        self.resources.pop(resource,None)

    async def generate(self, prompt, system, output, *, cached_context=None):
        self.reads.append(deepcopy(cached_context))
        result = await super().generate(prompt, system, output)
        if cached_context:
            result["usage"].update(input_tokens=cached_context["token_count"]+10,
                cached_tokens=cached_context["token_count"], uncached_input_tokens=10, reasoning_tokens=20)
        return result


async def setup(p=None, text="Evidence. "*1400):
    p=p or CachedProvider(); repo=Memory(); st=state(sources=[source(text)])
    st.corpus=await Builder(p).build(st.sources, "")
    await repo.create(st)
    return p,repo,st


@pytest.mark.asyncio
async def test_entire_editorial_cycle_reuses_one_cache_without_reingestion():
    s=source("Evidence. "*1400); repo=Memory(); st=state(request([dict(uri=s.origin)]))
    p=CachedProvider([*conception("A narrative."), *conception("A new direction."),
        {"title":"Work","body":complete_body(),"support":[support(s,"Evidence.")]}, *review_ok()])
    ing=Ingestor([s]); editor=Editor(repo,p,ing,Search(source(name="B")))
    await repo.create(st); await editor.execute(st,Calls(repo,p,st))
    r=Remoque(message="Change the direction",base_version=1);st.remoque.append(r)
    st.action=dict(kind="revise",id="r",remoque_hash=identity(r),version=1)
    await editor.execute(st,Calls(repo,p,st))
    st.action=dict(kind="approve",id="a",version=2);await editor.execute(st,Calls(repo,p,st))
    assert st.status=="text_ready" and ing.calls==1
    assert len(p.creations)==1 and len(p.reads)==9
    assert len({v["resource"] for v in p.reads if v})==1
    assert all("ORIGINAL TEXT" not in prompt for prompt,_ in p.prompts)
    assert p.creations[0][0]==st.corpus.blocks[0]
    assert all(ttl==3600 for _,ttl in p.renewals)
    usage=public(st)["usage"]
    assert usage["cache_creations"]==1 and usage["cache_reuses"]==2
    assert usage["reasoning_tokens"]==60 and usage["uncached_input_tokens"]==90
    assert usage["cached_tokens"]==3*st.corpus.block_tokens[0]


@pytest.mark.asyncio
async def test_new_source_and_manifest_create_new_cache_preserve_project():
    p,repo,st=await setup(); manager=Contexts(repo,p,st)
    old,_,_=await manager.prepare(st.corpus.blocks[0]+"\nPlan", "pe")
    st.sources.append(source("New reliable evidence. "*500,name="B"))
    st.corpus=await Builder(p).build(st.sources, "")
    new,_,_=await manager.prepare(st.corpus.blocks[0]+"\nRevised plan", "pe")
    assert old["resource"]!=new["resource"] and len(p.creations)==2
    assert old["identity"]["corpus_manifest"]!=new["identity"]["corpus_manifest"]
    assert "New reliable evidence." in p.creations[-1][0]
    assert old["resource"] not in p.resources


@pytest.mark.asyncio
@pytest.mark.parametrize("loss",["expired","deleted"])
async def test_lost_cache_recreated_from_persistent_corpus_after_restart(loss):
    p,repo,st=await setup(); first,_,_=await Contexts(repo,p,st).prepare(st.corpus.blocks[0], "pe")
    saved=deepcopy(st.model_dump(mode="json")); key=next(iter(repo.ops))
    if loss=="expired": repo.ops[key]["expire_at"]=(datetime.now(UTC)-timedelta(seconds=1)).isoformat()
    else:p.resources.clear()
    recovered=await repo.get(st.execution_id)
    second,_,_=await Contexts(repo,p,recovered).prepare(recovered.corpus.blocks[0], "remoque")
    assert first["resource"]!=second["resource"] and len(p.creations)==2
    assert p.creations[0][0]==p.creations[1][0]
    for field in ("sources","corpus","request","projects","remoque","work"):
        assert saved[field]==recovered.model_dump(mode="json")[field]
    assert any(t["cache_status"]=="recreated" for t in recovered.telemetry)


@pytest.mark.asyncio
async def test_small_corpus_is_explicitly_not_eligible():
    p,repo,st=await setup(CachedProvider([{"document":"Plan"}]),text="Small evidence.")
    await Calls(repo,p,st).structured("pe",st.corpus.blocks[0]+"\nDirection",Proposal)
    assert not p.creations and p.reads==[None]
    assert st.telemetry[-1]["cache_mode"]=="inline-not-eligible"
    assert st.telemetry[-1]["cache_status"]=="not_eligible" and "2048" in st.telemetry[-1]["cache_reason"]


@pytest.mark.asyncio
async def test_creation_failure_pauses_then_retries_only_missing_operation():
    p,repo,st=await setup(CachedProvider([{"document":"Plan"}]))
    p.create_error=RuntimeError("quota unavailable")
    prompt=st.corpus.blocks[0]+"\nDirection"
    with pytest.raises(Recoverable,match="without inline fallback"):
        await Calls(repo,p,st).structured("pe",prompt,Proposal)
    assert not p.prompts and st.sources and st.corpus
    assert any(t.get("cache_status")=="error" for t in (await repo.get(st.execution_id)).telemetry)
    p.create_error=None
    await Calls(repo,p,st).structured("pe",prompt,Proposal)
    await Calls(repo,p,st).structured("pe",prompt,Proposal)
    assert len(p.prompts)==1


@pytest.mark.asyncio
@pytest.mark.parametrize("change",["model","location"])
async def test_model_or_endpoint_change_never_reuses_cache(change):
    p,repo,st=await setup(); old,_,_=await Contexts(repo,p,st).prepare(st.corpus.blocks[0], "pe")
    setattr(p,change,"other-verified-fixture")
    if change=="model":p.cap=replace(p.cap,model=p.model)
    new,_,_=await Contexts(repo,p,st).prepare(st.corpus.blocks[0], "pe")
    assert old["resource"]!=new["resource"] and len(p.creations)==2


@pytest.mark.asyncio
async def test_two_blocks_get_only_two_resources_and_no_third_synthesis_cache():
    p,repo,st=await setup()
    blocks=[st.corpus.blocks[0],"Second block. "*1000]
    st.corpus=st.corpus.model_copy(update=dict(blocks=blocks,block_tokens=[await p.count(x) for x in blocks]))
    manager=Contexts(repo,p,st)
    resources=[]
    for cycle in range(2):
        for block in blocks:
            context,dynamic,_=await manager.prepare(block+"\nRead", "reading")
            assert dynamic=="\nRead";resources.append(context["resource"])
    assert len(set(resources))==2 and len(p.creations)==2
    context,_,_=await manager.prepare("GROUNDED READINGS:\nA + B + original support", "pe")
    assert context is None and len(p.creations)==2


@pytest.mark.asyncio
async def test_cache_resource_is_not_an_editorial_operation_identity():
    p,repo,st=await setup(CachedProvider([{"document":"Plan"}]))
    prompt=st.corpus.blocks[0]+"\nDirection"
    first=await Calls(repo,p,st).structured("pe",prompt,Proposal)
    operations=[k for k,v in repo.ops.items() if v.get("complete")]
    p.resources.clear() # completed logical operation does not need a new cache
    second=await Calls(repo,p,st).structured("pe",prompt,Proposal)
    assert first==second and len(p.creations)==1 and len(p.prompts)==1
    assert operations==[k for k,v in repo.ops.items() if v.get("complete")]


@pytest.mark.asyncio
async def test_unknown_creation_response_is_recovered_without_duplicate_creation():
    p,repo,st=await setup();manager=Contexts(repo,p,st)
    await manager.prepare(st.corpus.blocks[0], "pe")
    key=next(iter(repo.ops));repo.ops[key].pop("resource");repo.ops[key]["creating"]=True
    await manager.prepare(st.corpus.blocks[0], "pe")
    assert len(p.creations)==1


@pytest.mark.asyncio
async def test_cache_transport_uses_sdk_resource_without_system_or_corpus_duplication():
    p,models=vertex_fixture();p.project="p";p.location="global"
    now=datetime.now(UTC)
    meta=types.CachedContent(name="projects/p/locations/global/cachedContents/c",model=p.model,
        create_time=now,expire_time=now+timedelta(hours=1),
        usage_metadata=types.CachedContentUsageMetadata(total_token_count=3000))
    caches=SimpleNamespace(create=AsyncMock(return_value=meta),get=AsyncMock(return_value=meta),
                           update=AsyncMock(return_value=meta))
    p.client.aio.caches=caches;cap=await p.capacity()
    cache=await p.cache_create("Stable addressed corpus", "fixture",cap)
    config=caches.create.await_args.kwargs["config"]
    assert config.contents=="Stable addressed corpus" and config.system_instruction is None and config.ttl=="3600s"
    await p.cache_get(cache["resource"]);await p.cache_renew(cache["resource"],3600)
    result=await p.generate("Brief + PE + schema", "Editorial instructions",cap.output_tokens,cached_context=cache)
    args=models.generate_content.await_args.kwargs;config=args["config"]
    assert config.cached_content==cache["resource"] and config.system_instruction is None
    assert "Stable addressed corpus" not in args["contents"]
    assert "Editorial instructions" in args["contents"] and "Brief + PE + schema" in args["contents"]
    assert config.thinking_config.thinking_budget==24576 and config.temperature==1.0
    assert result["usage"]["reasoning_tokens"]==2048 and result["usage"]["uncached_input_tokens"]==600
    assert Decimal(result["usage"]["cost_usd"])==Decimal("0.005512")


@pytest.mark.asyncio
async def test_cached_tokens_count_once_and_dynamic_output_still_preflighted():
    p,repo,st=await setup(CachedProvider([{"document":"Plan"}]))
    p.cap=replace(p.cap,input_tokens=st.corpus.block_tokens[0]+p.cap.output_tokens+1500)
    await Calls(repo,p,st).structured("pe",st.corpus.blocks[0]+"\nDirection",Proposal)
    assert len(p.prompts)==1
    with pytest.raises(CapacityError):
        await Calls(repo,p,st).structured("oversized",st.corpus.blocks[0]+"\n"+"x"*10000,Proposal)
    assert len(p.prompts)==1


@pytest.mark.asyncio
async def test_unverified_cache_profile_pauses_without_generation():
    p,repo,st=await setup();p.cap=replace(p.cap,cache_min_tokens=None)
    with pytest.raises(Recoverable,match="verified cache_min_tokens"):
        await Calls(repo,p,st).structured("pe",st.corpus.blocks[0],Proposal)
    assert not p.creations and not p.prompts


@pytest.mark.asyncio
async def test_localized_correction_uses_same_cache_and_preserves_other_passages():
    p,repo,st=await setup(CachedProvider(conception("Narrative.")))
    s=st.sources[0];editor=Editor(repo,p,Ingestor([]),Search(s))
    await editor.execute(st,Calls(repo,p,st))
    st.action=dict(kind="approve",id="produce",version=1)
    ref=dict(source_id=str(s.source_id),location_id=s.locations[0].location_id)
    opening=complete_body("Opening.");body=opening+"\n\nWrong claim.\n\nClosing."
    p.outputs=[{"title":"Work","body":body,"support":[]},review_ok()[0],
        dict(problems=[problem(passages=["p2"])],evidence_questions=[]),
        dict(strategy="Qualify the specific claim while preserving the surrounding work.",
             edits=[dict(passage_ids=["p2"],replacement="Evidence.")],
             support=[support(s,"Documentary contribution, not a literal quote.")]),
        confirmed(),*review_ok()[2:]]
    await editor.execute(st,Calls(repo,p,st))
    assert st.work.body==opening+"\n\nEvidence.\n\nClosing." and st.status=="text_ready"
    assert len(p.creations)==1 and len({x["resource"] for x in p.reads if x})==1



@pytest.mark.asyncio
async def test_reuse_permission_failure_never_falls_back_to_inline():
    p,repo,st=await setup(CachedProvider([{"document":"Plan"}]))
    manager=Contexts(repo,p,st);await manager.prepare(st.corpus.blocks[0],"pe")
    p.cache_get=AsyncMock(side_effect=RuntimeError("permission denied"))
    with pytest.raises(Recoverable,match="without inline fallback"):
        await Calls(repo,p,st).structured("remoque",st.corpus.blocks[0]+"\nDirection",Proposal)
    assert len(p.creations)==1 and not p.prompts


@pytest.mark.asyncio
async def test_raw_response_repair_does_not_reconsult_cached_corpus():
    p,repo,st=await setup(CachedProvider(["{malformed",{"document":"Repaired"}]))
    # Provider fixture serializes strings, so this is valid JSON of wrong schema;
    # deterministic validation fails and the existing repair journal is exercised.
    result=await Calls(repo,p,st).structured("pe",st.corpus.blocks[0]+"\nDirection",Proposal)
    assert result.document=="Repaired" and len(p.creations)==1
    assert p.reads[0] and p.reads[1] is None
    assert st.corpus.blocks[0] not in p.prompts[1][0]


@pytest.mark.asyncio
async def test_large_cache_uses_immutable_gcs_transport(monkeypatch):
    from unittest.mock import MagicMock, patch
    p,_=vertex_fixture();p.project="p";p.location="global"
    cap=replace(await p.capacity(),cache_inline_max_bytes=10)
    now=datetime.now(UTC)
    metadata=types.CachedContent(name="projects/p/locations/global/cachedContents/c",model=p.model,
        create_time=now,expire_time=now+timedelta(hours=1),
        usage_metadata=types.CachedContentUsageMetadata(total_token_count=3000))
    p.client.aio.caches=SimpleNamespace(create=AsyncMock(return_value=metadata))
    monkeypatch.setenv("HEFESTO_GCS_BUCKET","fixture-bucket")
    client=MagicMock()
    with patch("google.cloud.storage.Client",return_value=client):
        await p.cache_create("Stable addressed corpus exceeds inline limit", "cache-key",cap)
    client.bucket.return_value.blob.return_value.upload_from_string.assert_called_once_with(
        "Stable addressed corpus exceeds inline limit",content_type="text/plain",if_generation_match=0)
    cfg=p.client.aio.caches.create.await_args.kwargs["config"]
    assert cfg.contents[0].parts[0].file_data.file_uri=="gs://fixture-bucket/hefesto-v8/context/cache-key.txt"
    assert cfg.contents[0].parts[0].file_data.mime_type=="text/plain"


@pytest.mark.asyncio
async def test_postgres_explicit_cache_metadata_survives_independent_worker_restart():
    import os
    from pathlib import Path
    from comunicamundo_v4.hefesto.postgres import ProductRepository
    from comunicamundo_v4.hefesto.v8.repository import Repository
    url=os.environ.get("V8_TEST_POSTGRES_URL")
    if not url:pytest.skip("isolated PostgreSQL integration database not configured")
    product=ProductRepository(url);await product.open()
    p,_,st=await setup(); repo=Repository(product)
    try:
        async with product._pool.connection() as c:
            await c.execute("CREATE SCHEMA IF NOT EXISTS hefesto_product")
            await c.execute((Path(__file__).parents[1]/"migrations/1008_hefesto_v8.sql").read_text())
        await repo.create(st)
        async with repo.lock(st.execution_id):
            first,_,_=await Contexts(repo,p,st).prepare(st.corpus.blocks[0],"pe")
    finally:await product.close()
    fresh=ProductRepository(url);await fresh.open()
    try:
        repo=Repository(fresh)
        async with repo.lock(st.execution_id):
            recovered=await repo.get(st.execution_id)
            second,_,_=await Contexts(repo,p,recovered).prepare(recovered.corpus.blocks[0],"remoque")
            assert first["resource"]==second["resource"] and len(p.creations)==1
            assert recovered.sources[0].text==st.sources[0].text
            assert public(recovered)["usage"]["cache_reuses"]==1
    finally:await fresh.close()


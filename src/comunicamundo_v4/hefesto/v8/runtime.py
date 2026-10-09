from contextlib import asynccontextmanager
import os
from uuid import UUID, uuid5
from google.cloud import storage
from ..postgres import ProductRepository
from ..source_pipeline import SourceAcquirer, SourceDiscovery, YouSearchProvider, OpenAlexSearchProvider
from .contracts import Input, State, Remoque, identity
from .repository import Repository, Busy
from .provider import Vertex, Calls
from .ingestion import Ingestor
from .hermes import Hermes
from .engine import Editor


def required(name):
    value=os.environ.get(name,"").strip()
    if not value: raise RuntimeError("missing environment variable "+name)
    return value


@asynccontextmanager
async def repository():
    url=required("HEFESTO_POSTGRES_DIRECT_URL")
    from psycopg.conninfo import conninfo_to_dict
    if "-pooler" in conninfo_to_dict(url).get("host", ""):
        raise RuntimeError("V8 requires a direct PostgreSQL connection for session advisory locks")
    product=ProductRepository(url)
    await product.open()
    try: yield Repository(product)
    finally: await product.close()


def public(state):
    data=state.model_dump(mode="json")
    data.pop("internal_plans",None)
    data.pop("review_checkpoint",None)
    data.pop("planning_checkpoint",None)
    data.pop("writing_checkpoint",None)
    data["work_versions"]=[]  # Intermediate manuscripts remain durable, not delivered as completed works.
    if state.status!="text_ready":data["work"]=None
    data["editorial_stages"]=[dict(position=v.position,stage_id=v.stage_id,status=v.status,
        approved_at=v.approval.get("approved_at") if v.approval else None) for v in state.editorial_stages]
    # Text and original addresses remain in durable state; avoid transferring
    # the full corpus and search acquisitions on every polling request.
    data["sources"]=[{k:v for k,v in s.model_dump(mode="json").items() if k not in {"text","metadata"}}
                     for s in state.sources]
    if data["corpus"]: data["corpus"].pop("blocks",None)
    data["research"]={k:{field:value for field,value in v.items() if field!="acquired"}
                      for k,v in state.research.items()}
    cache_events=[v for v in state.telemetry if v.get("kind")=="cache"]
    calls=[v for v in state.telemetry if v.get("kind")!="cache"]
    data["usage"]=dict(call_count=len(calls),input_tokens=sum(v.get("input_tokens",0) for v in calls),
        output_tokens=sum(v.get("output_tokens",0) for v in calls),cached_tokens=sum(v.get("cached_tokens",0) for v in calls),
        latency_ms=sum(v.get("latency_ms",0) for v in calls), cost_unknown_calls=sum(bool(v.get("cost_unknown")) for v in calls),
        reasoning_tokens=sum(v.get("reasoning_tokens",0) for v in calls),
        uncached_input_tokens=sum(v.get("uncached_input_tokens",max(0,v.get("input_tokens",0)-v.get("cached_tokens",0))) for v in calls),
        cache_creations=sum(v.get("cache_status") in {"created","recreated"} for v in cache_events),
        cache_reuses=sum(v.get("cache_status")=="reused" for v in cache_events),
        cache_recreations=sum(v.get("cache_status")=="recreated" for v in cache_events),
        cache_creation_input_tokens=sum(v.get("input_tokens",0) for v in cache_events),
        cache_storage_cost_estimate_usd=str(sum(__import__('decimal').Decimal(v.get("cache_storage_cost_estimate_usd","0")) for v in cache_events)),
        cost_usd=str(sum(__import__('decimal').Decimal(v.get("cost_usd","0")) for v in state.telemetry)))
    # Storage receipts are accrued through activity; the lease estimate also
    # exposes remaining TTL liability, including caches left idle to expire.
    from datetime import datetime
    from decimal import Decimal
    resources={v["resource"]:v for v in cache_events if v.get("resource")}
    storage_lease=sum((Decimal(v["token_count"])*Decimal(v["cache_storage_price"])*
        Decimal(str(max(0,(datetime.fromisoformat(v["expire_at"])-datetime.fromisoformat(v["created_at"])).total_seconds())))/Decimal(3600*1000000)
        for v in resources.values() if v.get("created_at") and v.get("expire_at")),Decimal(0))
    data["usage"]["cache_storage_lease_estimate_usd"]=str(storage_lease)
    data["usage"]["total_with_cache_storage_estimate_usd"]=str(Decimal(data["usage"]["cost_usd"])+storage_lease)
    data["usage"]["cache_resources"]=list(resources.values())
    if state.work:
        data["word_count"]=len(state.work.body.split())
        data["estimated_duration_minutes"]=data["word_count"]/150
    return data


class Backend:
    def __init__(self,dispatch):
        self.dispatch=dispatch

    async def _dispatch(self,repo,state):
        await repo.save(state)
        try:
            operation=await self.dispatch(["v8-run",f"--execution-run-id={state.execution_id}"])
            state.job={"operation":operation,"status":"queued"}
            await repo.save(state)
        except Exception as error:
            state.status="paused"
            state.error=dict(type=type(error).__name__,message=str(error),recoverable=True)
            await repo.save(state)
            raise
        return public(state)

    async def submit(self,request: Input,owner_id):
        eid=uuid5(request.project_id,"v8:"+identity(request))
        async with repository() as repo:
            async with repo.lock(eid):
                initial=State(execution_id=eid,owner_id=owner_id,request=request,
                              action=dict(kind="plan",id=identity(["plan",request])))
                await repo.create(initial)
                state=await repo.get(eid)
                if state.owner_id!=owner_id: raise PermissionError("project belongs to another user")
                if state.job and state.status!="paused":return public(state)
                return await self._dispatch(repo,state)

    async def get(self,eid,owner_id):
        async with repository() as repo:
            state=await repo.get(eid)
            if state.owner_id!=owner_id:raise PermissionError("project belongs to another user")
            return public(state)

    async def list(self,owner_id):
        async with repository() as repo:return await repo.list(owner_id)

    async def action(self,eid,owner_id,kind,version=None,message=None):
        async with repository() as repo:
            async with repo.lock(eid):
                state=await repo.get(eid)
                if state.owner_id!=owner_id:raise PermissionError("project belongs to another user")
                if kind=="resume":
                    if state.status not in {"paused","queued","ingesting","planning","searching_sources","writing","verifying"}:
                        raise ValueError("this state does not need resume")
                    if (state.error or {}).get("requires_retry_ack"):
                        state.action["retry_unknown_response"]=True
                    # Keep exact action/input identity; completed provider work
                    # stays in the journal and cannot be duplicated accidentally.
                    return await self._dispatch(repo,state)
                if not state.projects or version!=state.projects[-1].version:
                    raise ValueError("action requires the current PE version")
                action=dict(kind=kind,version=version)
                if kind=="revise":
                    if state.status not in {"project_proposed","project_approved","text_ready","no_reliable_support","project_too_large"}:
                        raise ValueError("project is not awaiting editorial interaction")
                    remoque=Remoque(message=message,base_version=version)
                    action["remoque_hash"]=identity(remoque)
                    action["base_status"]=state.status
                    if not any(identity(r)==identity(remoque) for r in state.remoque):state.remoque.append(remoque)
                elif kind in {"approve","produce"}:
                    from .editorial import linked_planning,dimensioned
                    from datetime import UTC,datetime
                    pe=state.projects[-1];plan=linked_planning(state,pe)
                    if not dimensioned(plan):
                        raise ValueError("Planejamento anterior preservado; solicite um remoque para consolidar o dimensionamento antes da aprovação.")
                    if kind=="produce" and not pe.approved_at:
                        raise ValueError("approved PE required")
                    action=dict(kind="approve",version=version,planning_id=str(plan.planning_id))
                    action["id"]=identity(action)
                    if action["id"] in state.completed_actions or state.status=="text_ready":return public(state)
                    if state.action.get("id")==action["id"] and state.status!="paused":return public(state)
                    if state.status!="project_proposed" and not (
                            state.status=="paused" and state.action.get("id")==action["id"]):
                        raise ValueError("project is not awaiting approval")
                    stamp=pe.approved_at or datetime.now(UTC).isoformat()
                    pe.approved_at=plan.approved_at=stamp
                    # Approval and automatic production identity are durable
                    # before dispatch; an interrupted dispatch is retryable.
                    state.action=action;state.status="queued";state.error=None;state.notice=None
                    return await self._dispatch(repo,state)
                else:raise ValueError("unknown action")
                action["id"]=identity(action)
                if action["id"] in state.completed_actions:return public(state)
                state.action=action
                state.status="queued"
                state.error=None
                state.notice=None
                return await self._dispatch(repo,state)


async def run(execution_id: UUID):
    async with repository() as repo:
        async with repo.lock(execution_id):
            state=await repo.get(execution_id)
            try:
                provider=Vertex(required("GOOGLE_CLOUD_PROJECT"),
                    os.environ.get("HEFESTO_VERTEX_LOCATION","global"),
                    os.environ.get("HEFESTO_V8_MODEL",os.environ.get("HEFESTO_MODEL","gemini-2.5-flash")))
                client=storage.Client(project=required("GOOGLE_CLOUD_PROJECT"))
                acquirer=SourceAcquirer(you_api_key=os.environ.get("YOU_API_KEY",""),
                    jina_api_key=os.environ.get("JINA_API_KEY",""),
                    browser_use_api_key=os.environ.get("BROWSER_USE_API_KEY", ""),
                    use_browser=bool(os.environ.get("BROWSER_USE_API_KEY")),
                    acceptable=lambda text: bool(text.strip()))
                providers=[]
                if os.environ.get("YOU_API_KEY"):providers.append(YouSearchProvider(os.environ["YOU_API_KEY"]))
                if os.environ.get("OPENALEX_API_KEY"):providers.append(OpenAlexSearchProvider(os.environ["OPENALEX_API_KEY"]))
                # Provided corpus works without search credentials. Hermes exposes a
                # real configuration pause if a later remoque needs research.
                discovery=SourceDiscovery(providers) if providers else None
                ingestor=Ingestor(client,required("HEFESTO_GCS_BUCKET"),provider,acquirer)
                hermes=Hermes(repo,discovery,acquirer,ingestor)
                calls=Calls(repo,provider,state)
                editor=Editor(repo,provider,ingestor,hermes)
                state.job["status"]="running"
                await repo.save(state)
                await editor.execute(state,calls)
            except Exception as error:
                state.status="paused"
                state.error=dict(type=type(error).__name__,message=str(error),recoverable=True,
                    requires_retry_ack=bool(getattr(error,"requires_retry_ack",False)))
            finally:
                state.job["status"]="completed" if state.status not in {"paused","project_too_large"} else "paused"
                await repo.save(state)
            return public(state)

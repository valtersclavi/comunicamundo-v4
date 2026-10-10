import asyncio
from pathlib import Path
from uuid import UUID
from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import Field
from .contracts import Input, Model
from .repository import Busy
from .runtime import Backend


class Action(Model):
    version: int = Field(ge=1)
    message: str | None = None


def install(app,owner,access,dispatcher_factory,backend=None):
    async def dispatch(args):
        return await asyncio.to_thread(dispatcher_factory().run,args)
    backend=backend or Backend(dispatch)

    async def invoke(method,*args):
        try:return await getattr(backend,method)(*args)
        except KeyError as e:raise HTTPException(404,str(e)) from e
        except PermissionError as e:raise HTTPException(403,str(e)) from e
        except (Busy,ValueError) as e:raise HTTPException(409,str(e)) from e
        except Exception as e:raise HTTPException(503,str(e)) from e

    @app.get("/",include_in_schema=False)
    @app.get("/v8",include_in_schema=False)
    async def page():
        root=Path(__file__).resolve().parents[2]/"web"
        return HTMLResponse((root/"v8.html").read_text(),headers={"Cache-Control":"no-store"})

    @app.get("/v8/projects")
    async def projects(request: Request):
        return {"projects":await invoke("list",await owner(request))}

    @app.post("/v8/projects",status_code=202)
    async def submit(payload: Input,request: Request):
        user=await owner(request)
        for source in payload.sources:
            if source.uri.startswith("gs://") and not await access.owns_source(user,source.uri):
                raise HTTPException(403,"stored source does not belong to this user")
        return await invoke("submit",payload,user)

    @app.get("/v8/projects/{execution_id}")
    async def get(execution_id: UUID,request: Request):
        return await invoke("get",execution_id,await owner(request))

    @app.post("/v8/projects/{execution_id}/resume",status_code=202)
    async def resume(execution_id: UUID,request: Request):
        return await invoke("action",execution_id,await owner(request),"resume")

    @app.post("/v8/projects/{execution_id}/{kind}",status_code=202)
    async def act(execution_id: UUID,kind: str,payload: Action,request: Request):
        if kind not in {"revise","approve","produce"}:raise HTTPException(404,"unknown V8 action")
        if kind=="revise" and not (payload.message or "").strip():raise HTTPException(422,"remoque message required")
        return await invoke("action",execution_id,await owner(request),kind,payload.version,payload.message)

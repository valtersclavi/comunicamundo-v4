import asyncio
import inspect
from uuid import uuid4

import httpx
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.indices.property_graph.base import PropertyGraphIndex
from llama_index.core.indices.property_graph.transformations.simple_llm import (
    SimpleLLMPathExtractor,
)
from llama_index.core.llms import MockLLM

from comunicamundo_v4.hefesto.contracts import NormalizedSource
from comunicamundo_v4.hefesto.graph import ProjectGraph, SyncLLMPathExtractor


class RecordingLLM(MockLLM):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    def predict(self, *args, **kwargs):
        self.calls.append("predict")
        return super().predict(*args, **kwargs)

    async def apredict(self, *args, **kwargs):
        self.calls.append("apredict")
        return await super().apredict(*args, **kwargs)


class RecordingEmbedding(MockEmbedding):
    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.calls: list[str] = []

    def _get_text_embedding(self, text: str):
        self.calls.append("get")
        return super()._get_text_embedding(text)

    async def _aget_text_embedding(self, text: str):
        self.calls.append("aget")
        return await super()._aget_text_embedding(text)


def _source() -> NormalizedSource:
    return NormalizedSource(
        source_id=uuid4(),
        text="A criança participa do luto com sua própria forma de compreensão.",
        language="pt",
    )


async def test_graph_survives_the_human_approval_boundary(tmp_path) -> None:
    llm = MockLLM(max_tokens=64)
    embedding = MockEmbedding(embed_dim=8)
    graph = await asyncio.to_thread(
        ProjectGraph.build,
        [_source()],
        llm=llm,
        embed_model=embedding,
    )
    storage_path = tmp_path / "project-graph"

    await asyncio.to_thread(graph.persist, storage_path)
    loaded = await asyncio.to_thread(
        ProjectGraph.load,
        storage_path,
        llm=llm,
        embed_model=embedding,
    )

    assert loaded.index.index_id == graph.index.index_id
    assert type(loaded.index.property_graph_store).__name__ == "SimplePropertyGraphStore"


def test_library_simple_extractor_and_property_graph_still_default_to_asyncio_run() -> None:
    assert "asyncio.run" in inspect.getsource(SimpleLLMPathExtractor.__call__)
    insert = inspect.getsource(PropertyGraphIndex._insert_nodes)
    assert insert.count("asyncio.run") == 3
    assert "use_async: bool = True" in inspect.getsource(PropertyGraphIndex.__init__)


def test_shared_async_http_client_dies_on_the_second_asyncio_run() -> None:
    """google.genai always constructs httpx.AsyncClient; PropertyGraphIndex then
    calls asyncio.run three times on a worker thread. The second run is the crash.
    """

    async def asgi_app(scope, receive, send) -> None:
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"text/plain")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    async def construct_and_reuse() -> tuple[str, str]:
        client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=asgi_app),
            base_url="http://graph-probe",
        )

        async def ping() -> str:
            response = await client.get("/")
            return str(response.status_code)

        def two_runs() -> tuple[str, str]:
            first = asyncio.run(ping())
            try:
                second = asyncio.run(ping())
            except Exception as error:
                second = f"{type(error).__name__}: {error}"
            return first, second

        first, second = await asyncio.to_thread(two_runs)
        await client.aclose()
        return first, second

    first, second = asyncio.run(construct_and_reuse())
    assert first == "200"
    assert second.startswith("RuntimeError: Event loop is closed")


async def test_plan_graph_build_uses_sync_vertex_clients_from_the_worker_thread() -> None:
    llm = RecordingLLM(max_tokens=64)
    embedding = RecordingEmbedding(embed_dim=8)
    graph = await asyncio.to_thread(
        ProjectGraph.build,
        [_source()],
        llm=llm,
        embed_model=embedding,
    )

    assert graph.index._use_async is False
    extractors = [type(item).__name__ for item in graph.index._kg_extractors]
    assert extractors == ["SyncLLMPathExtractor", "ImplicitPathExtractor"]
    assert "apredict" not in llm.calls
    assert "predict" in llm.calls
    assert "aget" not in embedding.calls
    assert "get" in embedding.calls
    assert "asyncio.run" not in inspect.getsource(SyncLLMPathExtractor.__call__)

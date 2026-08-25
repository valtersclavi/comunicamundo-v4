import asyncio
import inspect
from uuid import uuid4

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.indices.property_graph.base import PropertyGraphIndex
from llama_index.core.indices.property_graph.transformations.simple_llm import (
    SimpleLLMPathExtractor,
)
from llama_index.core.llms import MockLLM

from comunicamundo_v4.hefesto.contracts import NormalizedSource
from comunicamundo_v4.hefesto.graph import ProjectGraph, SyncLLMPathExtractor


class RecordingLLM(MockLLM):
    def predict(self, *args, **kwargs):
        type(self)._calls.append("predict")
        return super().predict(*args, **kwargs)

    async def apredict(self, *args, **kwargs):
        type(self)._calls.append("apredict")
        return await super().apredict(*args, **kwargs)


class RecordingEmbedding(MockEmbedding):
    def _get_text_embedding(self, text: str):
        type(self)._calls.append("get")
        return super()._get_text_embedding(text)

    async def _aget_text_embedding(self, text: str):
        type(self)._calls.append("aget")
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
    """After constructors skip models.get(), this is the remaining plan RPC path.

    PropertyGraphIndex still defaults to use_async=True and three asyncio.run
    calls. SimpleLLMPathExtractor.__call__ still asyncio.runs acall even when
    the index is constructed with use_async=False, so KG extract would use
    GoogleGenAI._achat / client.aio from the worker thread.
    """

    assert "asyncio.run" in inspect.getsource(SimpleLLMPathExtractor.__call__)
    insert = inspect.getsource(PropertyGraphIndex._insert_nodes)
    assert insert.count("asyncio.run") == 3
    assert "use_async: bool = True" in inspect.getsource(PropertyGraphIndex.__init__)
    assert "run_transformations(" in insert
    assert "self._embed_model.get_text_embedding_batch" in insert


async def test_plan_graph_build_uses_sync_vertex_clients_from_the_worker_thread() -> None:
    RecordingLLM._calls = []
    RecordingEmbedding._calls = []
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
    assert "apredict" not in RecordingLLM._calls
    assert "predict" in RecordingLLM._calls
    assert "aget" not in RecordingEmbedding._calls
    assert "get" in RecordingEmbedding._calls
    assert "asyncio.run" not in inspect.getsource(SyncLLMPathExtractor.__call__)

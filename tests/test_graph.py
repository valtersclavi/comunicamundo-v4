import asyncio
from uuid import uuid4

from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM

from comunicamundo_v4.hefesto.contracts import NormalizedSource
from comunicamundo_v4.hefesto.graph import ProjectGraph


async def test_graph_survives_the_human_approval_boundary(tmp_path) -> None:
    llm = MockLLM(max_tokens=64)
    embedding = MockEmbedding(embed_dim=8)
    graph = await asyncio.to_thread(
        ProjectGraph.build,
        [
            NormalizedSource(
                source_id=uuid4(),
                text="A criança participa do luto com sua própria forma de compreensão.",
                language="pt",
            )
        ],
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


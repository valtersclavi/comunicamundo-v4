import pytest
from comunicamundo_v4.hefesto.contracts import EditorialBrief
from comunicamundo_v4.hefesto.graph import ProjectGraph


class Node:
    def __init__(self, text): self.text = text
    def get_content(self): return self.text
class Item:
    def __init__(self, text): self.node = Node(text)
class Retriever:
    async def aretrieve(self, query): return [Item("material A"), Item("material B"), Item("material A")]
class Index:
    def as_retriever(self, **kwargs): return Retriever()


async def test_understanding_uses_graph_retrieval_and_deduplicates() -> None:
    graph = ProjectGraph(index=Index())
    brief = EditorialBrief(theme="Tema", approach="Abordagem", duration_minutes=8)
    context = await graph.understand(brief)
    assert context.count("material A") == 1
    assert "material B" in context


def test_build_rejects_empty_sources() -> None:
    with pytest.raises(ValueError):
        ProjectGraph.build([], llm=object(), embed_model=object())

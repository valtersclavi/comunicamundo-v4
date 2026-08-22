"""LlamaIndex property-graph implementation for one Hefesto project."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from llama_index.core import (
    Document,
    PropertyGraphIndex,
    StorageContext,
    load_index_from_storage,
)
from llama_index.core.base.base_retriever import BaseRetriever
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.llms import LLM
from llama_index.core.schema import NodeWithScore

from .contracts import EditorialBrief, EditorialSubject, NormalizedSource
from .prompts import GRAPH_PLANNING_QUERY


@dataclass(slots=True)
class ProjectGraph:
    """A project-scoped graph and its retrieval surface."""

    index: PropertyGraphIndex

    @classmethod
    def build(
        cls,
        sources: Sequence[NormalizedSource],
        *,
        llm: LLM,
        embed_model: BaseEmbedding,
    ) -> ProjectGraph:
        documents = [
            Document(
                text=source.text,
                doc_id=str(source.source_id),
                metadata={
                    **source.metadata,
                    "source_id": str(source.source_id),
                    "language": source.language,
                    "title": source.title or "",
                },
            )
            for source in sources
        ]
        index = PropertyGraphIndex.from_documents(
            documents,
            llm=llm,
            embed_model=embed_model,
            embed_kg_nodes=True,
            use_async=True,
            show_progress=False,
        )
        return cls(index=index)

    @classmethod
    def load(
        cls,
        directory: Path,
        *,
        llm: LLM,
        embed_model: BaseEmbedding,
    ) -> ProjectGraph:
        storage_context = StorageContext.from_defaults(persist_dir=str(directory))
        index = load_index_from_storage(
            storage_context,
            llm=llm,
            embed_model=embed_model,
        )
        if not isinstance(index, PropertyGraphIndex):
            raise TypeError(f"expected PropertyGraphIndex, received {type(index).__name__}")
        return cls(index=index)

    def persist(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.index.storage_context.persist(persist_dir=str(directory))

    async def context_for_editorial_project(self, brief: EditorialBrief) -> str:
        query = GRAPH_PLANNING_QUERY.format(
            theme=brief.theme,
            approach=brief.approach,
            audience=brief.audience or "não especificado",
        )
        retriever: BaseRetriever = self.index.as_retriever(include_text=True)
        nodes = await retriever.aretrieve(query)
        return _render_nodes(nodes)

    async def context_for_subject(
        self,
        subject: EditorialSubject,
        brief: EditorialBrief,
    ) -> str:
        questions = "\n".join(f"- {question}" for question in subject.research_questions)
        query = (
            f"Tema geral: {brief.theme}\n"
            f"Abordagem: {brief.approach}\n"
            f"Assunto: {subject.title}\n"
            f"Função: {subject.purpose}\n"
            f"Perguntas:\n{questions}"
        )
        retriever: BaseRetriever = self.index.as_retriever(
            include_text=True,
            similarity_top_k=8,
            path_depth=2,
            limit=40,
        )
        nodes = await retriever.aretrieve(query)
        return _render_nodes(nodes)


def _render_nodes(nodes: Sequence[NodeWithScore]) -> str:
    rendered: list[str] = []
    for position, node_with_score in enumerate(nodes, start=1):
        node = node_with_score.node
        title = node.metadata.get("title") or node.metadata.get("source_id") or "fonte"
        content = node.get_content().strip()
        if not content:
            continue
        rendered.append(f"[{position}] {title}\n{content}")
    return "\n\n".join(rendered)

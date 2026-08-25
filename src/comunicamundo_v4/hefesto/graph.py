"""LlamaIndex property-graph implementation for one Hefesto project."""

from __future__ import annotations

import logging
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
from llama_index.core.graph_stores.types import (
    KG_NODES_KEY,
    KG_RELATIONS_KEY,
    EntityNode,
    Relation,
)
from llama_index.core.indices.property_graph.transformations import (
    ImplicitPathExtractor,
    SimpleLLMPathExtractor,
)
from llama_index.core.llms import LLM
from llama_index.core.schema import BaseNode, MetadataMode, NodeWithScore

from .contracts import EditorialBrief, EditorialSubject, NarrativeAxis, NormalizedSource
from .prompts import GRAPH_PLANNING_QUERY

logger = logging.getLogger(__name__)


class SyncLLMPathExtractor(SimpleLLMPathExtractor):
    """KG extract through the sync LLM client.

    LlamaIndex's SimpleLLMPathExtractor.__call__ always does
    ``asyncio.run(self.acall(...))``, even when PropertyGraphIndex is constructed
    with ``use_async=False``. That path uses GoogleGenAI._achat and the Vertex
    aio HTTP client. Hefesto builds the graph inside ``asyncio.to_thread`` after
    constructing those clients on the worker's running loop. A shared
    httpx.AsyncClient then dies on the second ``asyncio.run`` with
    ``RuntimeError: Event loop is closed``. Graph build therefore uses
    ``predict()`` / ``embed_content()`` instead of aio. ``use_async=False`` is
    still required so embeddings also stay on the sync client.
    """

    @classmethod
    def class_name(cls) -> str:
        return "SyncLLMPathExtractor"

    def __call__(
        self,
        nodes: Sequence[BaseNode],
        show_progress: bool = False,
        **kwargs: object,
    ) -> Sequence[BaseNode]:
        del show_progress, kwargs
        return [self._extract_sync(node) for node in nodes]

    def _extract_sync(self, node: BaseNode) -> BaseNode:
        assert hasattr(node, "text")
        text = node.get_content(metadata_mode=MetadataMode.LLM)
        try:
            llm_response = self.llm.predict(
                self.extract_prompt,
                text=text,
                max_knowledge_triplets=self.max_paths_per_chunk,
            )
            triples = self.parse_fn(llm_response)
        except ValueError as error:
            logger.error("Error during extraction: %s", error, exc_info=True)
            if self.raise_on_error:
                raise
            triples = []

        existing_nodes = node.metadata.pop(KG_NODES_KEY, [])
        existing_relations = node.metadata.pop(KG_RELATIONS_KEY, [])
        metadata = node.metadata.copy()
        for subject, relation, obj in triples:
            subject_node = EntityNode(name=subject, properties=metadata)
            object_node = EntityNode(name=obj, properties=metadata)
            existing_nodes.extend([subject_node, object_node])
            existing_relations.append(
                Relation(
                    label=relation,
                    source_id=subject_node.id,
                    target_id=object_node.id,
                    properties=metadata,
                )
            )
        node.metadata[KG_NODES_KEY] = existing_nodes
        node.metadata[KG_RELATIONS_KEY] = existing_relations
        return node


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
            kg_extractors=[
                SyncLLMPathExtractor(llm=llm),
                ImplicitPathExtractor(),
            ],
            embed_kg_nodes=True,
            use_async=False,
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
        narrative_axis: NarrativeAxis,
    ) -> str:
        query = (
            f"Tema geral: {brief.theme}\n"
            f"Abordagem/intenção: {brief.approach}\n"
            f"Eixo Narrativo aprovado: {narrative_axis.statement}\n"
            f"Assunto: {subject.title}\n"
            f"Desenvolvimento esperado: {subject.development}"
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

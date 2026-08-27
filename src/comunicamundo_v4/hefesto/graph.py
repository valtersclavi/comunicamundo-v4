"""Fresh LlamaIndex PropertyGraph implementation for one project run."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from llama_index.core import Document, PropertyGraphIndex, StorageContext, load_index_from_storage
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.graph_stores.types import EntityNode, KG_NODES_KEY, KG_RELATIONS_KEY, Relation
from llama_index.core.llms import LLM
from llama_index.core.schema import TextNode, TransformComponent

from .contracts import EditorialBrief, EditorialSubject, GraphExtraction, NormalizedSource
from .prompts import PROJECT_UNDERSTANDING_QUERY, SUBJECT_CONTEXT_QUERY


class PersistedKgTransform(TransformComponent):
    """Keep EntityNode/Relation objects already attached to node metadata.

    LlamaIndex 0.14.24 treats ``kg_extractors=[]`` as missing and reconstructs
    ``SimpleLLMPathExtractor``. A non-empty pass-through is required so assembly
    never builds or calls a graph-extraction LLM.
    """

    @classmethod
    def class_name(cls) -> str:
        return "PersistedKgTransform"

    def __call__(self, nodes: Sequence, **kwargs) -> list:
        return list(nodes)


def persisted_kg_extractors() -> list[TransformComponent]:
    return [PersistedKgTransform()]


def _text_for_extraction(extraction: GraphExtraction) -> str:
    if extraction.relations:
        return "\n".join(f"{item.source} {item.relation} {item.target}" for item in extraction.relations)
    if extraction.entities:
        return "\n".join(entity.name for entity in extraction.entities)
    return extraction.chunk_id


@dataclass(slots=True)
class ProjectGraph:
    index: PropertyGraphIndex

    @classmethod
    def build(cls, sources: Sequence[NormalizedSource], *, llm: LLM, embed_model: BaseEmbedding) -> "ProjectGraph":
        if not sources:
            raise ValueError("at least one normalized source is required")
        documents = [
            Document(
                text=source.text,
                doc_id=str(source.source_id),
                metadata={
                    "source_id": str(source.source_id),
                    "language": source.language,
                    "title": source.title or "",
                    **source.metadata,
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
    def from_extractions(cls, extractions: Sequence[GraphExtraction], *, embed_model: BaseEmbedding) -> "ProjectGraph":
        """Assemble a PropertyGraphIndex from durable 2A extractions. Embedding only."""
        from .graph_extraction import graph_extraction_to_llama

        if not extractions:
            raise ValueError("at least one persisted graph extraction is required")
        nodes: list[TextNode] = []
        for extraction in extractions:
            entities, relations = graph_extraction_to_llama(extraction)
            nodes.append(
                TextNode(
                    text=_text_for_extraction(extraction),
                    id_=extraction.chunk_id,
                    metadata={
                        KG_NODES_KEY: entities,
                        KG_RELATIONS_KEY: relations,
                        "chunk_id": extraction.chunk_id,
                        "source_id": str(extraction.source_id),
                        "source_lineage_id": str(extraction.source_lineage_id),
                    },
                )
            )
        index = PropertyGraphIndex(
            nodes=nodes,
            kg_extractors=persisted_kg_extractors(),
            embed_model=embed_model,
            embed_kg_nodes=True,
            use_async=False,
            show_progress=False,
        )
        return cls(index=index)

    @classmethod
    def load(cls, directory: Path, *, embed_model: BaseEmbedding, llm: LLM | None = None) -> "ProjectGraph":
        storage_context = StorageContext.from_defaults(persist_dir=str(directory))
        index = load_index_from_storage(
            storage_context,
            llm=llm,
            embed_model=embed_model,
            kg_extractors=persisted_kg_extractors(),
            embed_kg_nodes=True,
            use_async=False,
        )
        if not isinstance(index, PropertyGraphIndex):
            raise TypeError("persisted index is not a PropertyGraphIndex")
        return cls(index=index)

    def persist(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.index.storage_context.persist(persist_dir=str(directory))

    def entity_nodes(self) -> list[EntityNode]:
        return [node for node in self.index.property_graph_store.get() if isinstance(node, EntityNode)]

    def relations(self) -> list[Relation]:
        return [triplet[1] for triplet in self.index.property_graph_store.graph.get_triplets()]

    async def _retrieve(self, query: str, *, top_k: int) -> str:
        retriever = self.index.as_retriever(include_text=True, similarity_top_k=top_k)
        nodes = await retriever.aretrieve(query)
        material: list[str] = []
        for item in nodes:
            node = getattr(item, "node", item)
            text = node.get_content().strip()
            if text and text not in material:
                material.append(text)
        return "\n\n---\n\n".join(material)

    async def understand(self, brief: EditorialBrief) -> str:
        query = PROJECT_UNDERSTANDING_QUERY.format(
            theme=brief.theme,
            approach=brief.approach,
            audience=brief.audience or "not specified",
            product=brief.product,
            duration_minutes=brief.duration_minutes,
            output_language=brief.output_language,
        )
        material = await self._retrieve(query, top_k=24)
        if not material:
            raise ValueError("PropertyGraph returned no material for project understanding")
        return material

    async def context_for_subject(self, subject: EditorialSubject, *, narrative_axis: str) -> str:
        query = SUBJECT_CONTEXT_QUERY.format(
            narrative_axis=narrative_axis,
            subject=subject.title,
            development=subject.development,
        )
        return await self._retrieve(query, top_k=10)

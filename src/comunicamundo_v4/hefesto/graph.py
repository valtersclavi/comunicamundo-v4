"""Fresh LlamaIndex PropertyGraph implementation for one project run."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from llama_index.core import Document, PropertyGraphIndex, StorageContext, load_index_from_storage
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.llms import LLM

from .contracts import EditorialBrief, EditorialSubject, NormalizedSource
from .prompts import PROJECT_UNDERSTANDING_QUERY, SUBJECT_CONTEXT_QUERY


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
    def load(cls, directory: Path, *, llm: LLM, embed_model: BaseEmbedding) -> "ProjectGraph":
        storage_context = StorageContext.from_defaults(persist_dir=str(directory))
        index = load_index_from_storage(storage_context, llm=llm, embed_model=embed_model)
        if not isinstance(index, PropertyGraphIndex):
            raise TypeError("persisted index is not a PropertyGraphIndex")
        return cls(index=index)

    def persist(self, directory: Path) -> None:
        directory.mkdir(parents=True, exist_ok=True)
        self.index.storage_context.persist(persist_dir=str(directory))

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

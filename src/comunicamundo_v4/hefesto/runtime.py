"""Vertex AI runtime construction for the clean rebuild."""
from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
from pathlib import Path

from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from .editorial import EditorialDirector
from .graph import ProjectGraph
from .production import EditorialProduction


GEMINI_2_5_CONTEXT_WINDOW = 1_048_576


@dataclass(frozen=True, slots=True)
class VertexRuntimeSettings:
    project: str
    location: str = "us-central1"
    graph_model: str = "gemini-2.5-flash"
    axis_model: str = "gemini-2.5-pro"
    project_model: str = "gemini-2.5-pro"
    research_model: str = "gemini-2.5-flash"
    architecture_model: str = "gemini-2.5-pro"
    writing_model: str = "gemini-2.5-pro"
    critic_model: str = "gemini-2.5-flash"
    editing_model: str = "gemini-2.5-pro"
    embedding_model: str = "gemini-embedding-001"
    context_window: int = GEMINI_2_5_CONTEXT_WINDOW
    graph_max_tokens: int = 2_048
    axis_max_tokens: int = 1_200
    project_max_tokens: int = 5_000
    research_max_tokens: int = 4_096
    architecture_max_tokens: int = 5_000
    writing_max_tokens: int = 10_000
    critic_max_tokens: int = 3_500
    editing_max_tokens: int = 10_000


@dataclass(frozen=True, slots=True)
class GraphExtractionRuntime:
    llm: object
    model: str
    context_window: int
    max_tokens: int


@dataclass(frozen=True, slots=True)
class GraphIndexingRuntime:
    embed_model: object
    model: str
    package_version: str
    config: dict[str, object]


@dataclass(slots=True)
class HefestoRuntime:
    graph_llm: object
    embed_model: object
    director: EditorialDirector
    production: EditorialProduction

    def build_graph(self, sources):
        return ProjectGraph.build(sources, llm=self.graph_llm, embed_model=self.embed_model)

    def load_graph(self, directory: Path):
        return ProjectGraph.load(directory, llm=self.graph_llm, embed_model=self.embed_model)


def _vertex_llm(settings: VertexRuntimeSettings, model: str, temperature: float, max_tokens: int):
    return GoogleGenAI(
        model=model,
        temperature=temperature,
        max_tokens=max_tokens,
        context_window=settings.context_window,
        vertexai_config={"project": settings.project, "location": settings.location},
    )


def build_graph_extraction_runtime(settings: VertexRuntimeSettings) -> GraphExtractionRuntime:
    """Construct only the graph LLM for one-chunk extraction."""
    if settings.context_window <= 0:
        raise ValueError("graph extraction requires an explicit context_window")
    if settings.graph_max_tokens <= 0:
        raise ValueError("graph extraction requires an explicit max_tokens budget")
    return GraphExtractionRuntime(
        llm=_vertex_llm(settings, settings.graph_model, 0.1, settings.graph_max_tokens),
        model=settings.graph_model,
        context_window=settings.context_window,
        max_tokens=settings.graph_max_tokens,
    )


def build_graph_indexing_runtime(settings: VertexRuntimeSettings) -> GraphIndexingRuntime:
    """Construct only the embedding client required to index a persisted PropertyGraph."""
    if not settings.embedding_model.strip():
        raise ValueError("graph indexing requires an explicit embedding_model")
    embed_model = GoogleGenAIEmbedding(
        model_name=settings.embedding_model,
        vertexai_config={"project": settings.project, "location": settings.location},
    )
    return GraphIndexingRuntime(
        embed_model=embed_model,
        model=settings.embedding_model,
        package_version=version("llama-index-embeddings-google-genai"),
        config={
            "model_name": settings.embedding_model,
            "location": settings.location,
            "embed_kg_nodes": True,
        },
    )


def build_vertex_runtime(settings: VertexRuntimeSettings) -> HefestoRuntime:
    vertexai_config = {"project": settings.project, "location": settings.location}
    graph_llm = _vertex_llm(settings, settings.graph_model, 0.1, settings.graph_max_tokens)
    embed_model = GoogleGenAIEmbedding(model_name=settings.embedding_model, vertexai_config=vertexai_config)
    return HefestoRuntime(
        graph_llm=graph_llm,
        embed_model=embed_model,
        director=EditorialDirector(
            axis_llm=_vertex_llm(settings, settings.axis_model, 0.35, settings.axis_max_tokens),
            project_llm=_vertex_llm(settings, settings.project_model, 0.35, settings.project_max_tokens),
        ),
        production=EditorialProduction(
            research_llm=_vertex_llm(settings, settings.research_model, 0.2, settings.research_max_tokens),
            architecture_llm=_vertex_llm(settings, settings.architecture_model, 0.35, settings.architecture_max_tokens),
            writing_llm=_vertex_llm(settings, settings.writing_model, 0.65, settings.writing_max_tokens),
            critic_llm=_vertex_llm(settings, settings.critic_model, 0.15, settings.critic_max_tokens),
            editing_llm=_vertex_llm(settings, settings.editing_model, 0.45, settings.editing_max_tokens),
        ),
    )

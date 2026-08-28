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
    understanding_model: str = "gemini-2.5-pro"
    embedding_model: str = "gemini-embedding-001"
    context_window: int = GEMINI_2_5_CONTEXT_WINDOW
    # Gemini 2.5 thinking tokens count against max output. These planning
    # stages therefore need enough headroom for reasoning plus the final
    # compact response; the previous 2K/4K/1.2K caps could terminate before
    # the model emitted a usable answer even for one atomic chunk.
    graph_max_tokens: int = 12_000
    understanding_max_tokens: int = 16_384
    axis_max_tokens: int = 10_000
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


@dataclass(frozen=True, slots=True)
class AxisProposalRuntime:
    embed_model: object
    understanding_llm: object
    understanding_model: str
    understanding_max_tokens: int
    axis_llm: object
    axis_model: str
    axis_max_tokens: int
    context_window: int


@dataclass(frozen=True, slots=True)
class AxisFeedbackRuntime:
    axis_llm: object
    axis_model: str
    axis_max_tokens: int
    context_window: int


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


def build_axis_proposal_runtime(settings: VertexRuntimeSettings) -> AxisProposalRuntime:
    """Construct only understanding + axis LLMs and the embedding client to reload the graph."""
    if settings.context_window <= 0:
        raise ValueError("axis proposal requires an explicit context_window")
    if settings.understanding_max_tokens <= 0:
        raise ValueError("editorial understanding requires an explicit max_tokens budget")
    if settings.axis_max_tokens <= 0:
        raise ValueError("Narrative Axis proposal requires an explicit max_tokens budget")
    if not settings.embedding_model.strip():
        raise ValueError("axis proposal requires an explicit embedding_model to restore the PropertyGraph")
    embed_model = GoogleGenAIEmbedding(
        model_name=settings.embedding_model,
        vertexai_config={"project": settings.project, "location": settings.location},
    )
    return AxisProposalRuntime(
        embed_model=embed_model,
        understanding_llm=_vertex_llm(settings, settings.understanding_model, 0.2, settings.understanding_max_tokens),
        understanding_model=settings.understanding_model,
        understanding_max_tokens=settings.understanding_max_tokens,
        axis_llm=_vertex_llm(settings, settings.axis_model, 0.35, settings.axis_max_tokens),
        axis_model=settings.axis_model,
        axis_max_tokens=settings.axis_max_tokens,
        context_window=settings.context_window,
    )


def build_axis_feedback_runtime(settings: VertexRuntimeSettings) -> AxisFeedbackRuntime:
    """Construct only the Axis LLM for inspectable feedback decisions. No embeddings, graph, or understanding clients."""
    if settings.context_window <= 0:
        raise ValueError("axis feedback requires an explicit context_window")
    if settings.axis_max_tokens <= 0:
        raise ValueError("axis feedback requires an explicit max_tokens budget")
    return AxisFeedbackRuntime(
        axis_llm=_vertex_llm(settings, settings.axis_model, 0.35, settings.axis_max_tokens),
        axis_model=settings.axis_model,
        axis_max_tokens=settings.axis_max_tokens,
        context_window=settings.context_window,
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

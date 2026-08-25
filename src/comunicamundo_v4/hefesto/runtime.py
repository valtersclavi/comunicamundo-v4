"""Vertex AI runtime construction for the clean rebuild."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from .editorial import EditorialDirector
from .graph import ProjectGraph
from .production import EditorialProduction

# Published Gemini 2.5 input window. Passing it with a stage max_tokens keeps
# GoogleGenAI.__init__ from calling Vertex models.get().
GEMINI_2_5_CONTEXT_WINDOW = 1_048_576

# Stage output caps. Never a generic 65_536 budget.
GRAPH_EXTRACT_MAX_TOKENS = 2048
AXIS_MAX_TOKENS = 1200
PROJECT_MAX_TOKENS = 5000
RESEARCH_MAX_TOKENS = 4096
ARCHITECTURE_MAX_TOKENS = 5000
WRITING_MAX_TOKENS = 10000
CRITIC_MAX_TOKENS = 3500
EDITING_MAX_TOKENS = 10000


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


def build_vertex_runtime(settings: VertexRuntimeSettings) -> HefestoRuntime:
    vertexai_config = {"project": settings.project, "location": settings.location}

    def llm(model: str, temperature: float, max_tokens: int):
        return GoogleGenAI(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            context_window=GEMINI_2_5_CONTEXT_WINDOW,
            vertexai_config=vertexai_config,
        )

    graph_llm = llm(settings.graph_model, 0.1, GRAPH_EXTRACT_MAX_TOKENS)
    embed_model = GoogleGenAIEmbedding(model_name=settings.embedding_model, vertexai_config=vertexai_config)
    return HefestoRuntime(
        graph_llm=graph_llm,
        embed_model=embed_model,
        director=EditorialDirector(
            axis_llm=llm(settings.axis_model, 0.35, AXIS_MAX_TOKENS),
            project_llm=llm(settings.project_model, 0.35, PROJECT_MAX_TOKENS),
        ),
        production=EditorialProduction(
            research_llm=llm(settings.research_model, 0.2, RESEARCH_MAX_TOKENS),
            architecture_llm=llm(settings.architecture_model, 0.35, ARCHITECTURE_MAX_TOKENS),
            writing_llm=llm(settings.writing_model, 0.65, WRITING_MAX_TOKENS),
            critic_llm=llm(settings.critic_model, 0.15, CRITIC_MAX_TOKENS),
            editing_llm=llm(settings.editing_model, 0.45, EDITING_MAX_TOKENS),
        ),
    )

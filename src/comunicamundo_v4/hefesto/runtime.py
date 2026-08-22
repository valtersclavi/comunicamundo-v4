"""Vertex AI runtime assembly for Hefesto V4."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.llms import LLM
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from .graph import ProjectGraph
from .production_workflow import EditorialProductionWorkflow
from .workflow import EditorialPlanningWorkflow


@dataclass(frozen=True, slots=True)
class VertexRuntimeSettings:
    project: str
    location: str = "us-central1"
    graph_model: str = "gemini-2.5-flash"
    planning_model: str = "gemini-2.5-pro"
    research_model: str = "gemini-2.5-flash"
    architecture_model: str = "gemini-2.5-pro"
    writing_model: str = "gemini-2.5-pro"
    critic_model: str = "gemini-2.5-flash"
    editing_model: str = "gemini-2.5-pro"
    embedding_model: str = "gemini-embedding-001"


@dataclass(slots=True)
class HefestoRuntime:
    """All model-backed capabilities required by one worker process."""

    planning_workflow: EditorialPlanningWorkflow
    production_workflow: EditorialProductionWorkflow
    graph_llm: LLM
    embed_model: BaseEmbedding

    def load_graph(self, directory: Path) -> ProjectGraph:
        return ProjectGraph.load(
            directory,
            llm=self.graph_llm,
            embed_model=self.embed_model,
        )


def build_vertex_runtime(settings: VertexRuntimeSettings) -> HefestoRuntime:
    vertexai_config = {
        "project": settings.project,
        "location": settings.location,
    }

    def llm(model: str, temperature: float) -> GoogleGenAI:
        return GoogleGenAI(
            model=model,
            temperature=temperature,
            vertexai_config=vertexai_config,
        )

    graph_llm = llm(settings.graph_model, 0.1)
    embed_model = GoogleGenAIEmbedding(
        model_name=settings.embedding_model,
        vertexai_config=vertexai_config,
    )
    return HefestoRuntime(
        planning_workflow=EditorialPlanningWorkflow(
            graph_llm=graph_llm,
            planning_llm=llm(settings.planning_model, 0.4),
            embed_model=embed_model,
        ),
        production_workflow=EditorialProductionWorkflow(
            research_llm=llm(settings.research_model, 0.2),
            architecture_llm=llm(settings.architecture_model, 0.5),
            writing_llm=llm(settings.writing_model, 0.7),
            critic_llm=llm(settings.critic_model, 0.2),
            editing_llm=llm(settings.editing_model, 0.5),
        ),
        graph_llm=graph_llm,
        embed_model=embed_model,
    )


def build_vertex_planning_workflow(
    settings: VertexRuntimeSettings,
) -> EditorialPlanningWorkflow:
    return build_vertex_runtime(settings).planning_workflow


def build_vertex_production_workflow(
    settings: VertexRuntimeSettings,
) -> EditorialProductionWorkflow:
    return build_vertex_runtime(settings).production_workflow

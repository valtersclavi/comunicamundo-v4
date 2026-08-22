"""Vertex AI runtime assembly for Hefesto V4."""

from __future__ import annotations

from dataclasses import dataclass

from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from .workflow import EditorialPlanningWorkflow
from .production_workflow import EditorialProductionWorkflow


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


def build_vertex_planning_workflow(
    settings: VertexRuntimeSettings,
) -> EditorialPlanningWorkflow:
    vertexai_config = {
        "project": settings.project,
        "location": settings.location,
    }
    graph_llm = GoogleGenAI(
        model=settings.graph_model,
        temperature=0.1,
        vertexai_config=vertexai_config,
    )
    planning_llm = GoogleGenAI(
        model=settings.planning_model,
        temperature=0.4,
        vertexai_config=vertexai_config,
    )
    embed_model = GoogleGenAIEmbedding(
        model_name=settings.embedding_model,
        vertexai_config=vertexai_config,
    )
    return EditorialPlanningWorkflow(
        graph_llm=graph_llm,
        planning_llm=planning_llm,
        embed_model=embed_model,
    )


def build_vertex_production_workflow(
    settings: VertexRuntimeSettings,
) -> EditorialProductionWorkflow:
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

    return EditorialProductionWorkflow(
        research_llm=llm(settings.research_model, 0.2),
        architecture_llm=llm(settings.architecture_model, 0.5),
        writing_llm=llm(settings.writing_model, 0.7),
        critic_llm=llm(settings.critic_model, 0.2),
        editing_llm=llm(settings.editing_model, 0.5),
    )

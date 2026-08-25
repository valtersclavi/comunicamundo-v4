"""Vertex AI runtime assembly for Hefesto V4."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from llama_index.core.embeddings import BaseEmbedding
from llama_index.core.llms import LLM
from llama_index.embeddings.google_genai import GoogleGenAIEmbedding
from llama_index.llms.google_genai import GoogleGenAI

from .graph import ProjectGraph
from .planner import EditorialPlanner, NarrativeAxisPlanner
from .production_workflow import EditorialProductionWorkflow
from .workflow import EditorialPlanningWorkflow


# Published Gemini input window. Passed so llama-index skips client.models.get();
# it is not an output budget. The published output maximum (65_536) is never
# used as a stage cap: that metadata RPC needs serviceusage.services.use, and
# using it as max_tokens recreates token-explosion failures.
GEMINI_CONTEXT_WINDOW = 1_048_576
PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS = 65_536

# Stage output caps are derived from the artifact contract / expected stage
# output, not from the model maximum. Portuguese JSON is estimated at about
# 3–4 characters per token; caps include structured-output envelope.
#
# NarrativeAxisProposal.statement max_length=480.
AXIS_MAX_OUTPUT_TOKENS = 384
# EditorialProjectProposal: working_title<=200, <=20 subjects of title<=160
# + development<=480. Compact JSON ceiling, same cost circuit as research.
PROJECT_MAX_OUTPUT_TOKENS = 4096
# Per-chunk KG extraction (entities/relations), not a full article.
GRAPH_MAX_OUTPUT_TOKENS = 2048
# Incident 2026-08-22: 400–700 word research briefing, cost circuit.
RESEARCH_MAX_OUTPUT_TOKENS = 4096
# NarrativePlan: title, opening, thesis, <=20 beats, closing, <=8 voice notes.
ARCHITECTURE_MAX_OUTPUT_TOKENS = 3072
# Article body / rewrite. 8-minute proof pieces are ~1.5k words; 4096 is the
# same circuit as research, not the 65_536 model maximum.
WRITING_MAX_OUTPUT_TOKENS = 4096
EDITING_MAX_OUTPUT_TOKENS = 4096
# EditorialReview: score + compact lists (max 8 / 12 / 12 notes).
CRITIC_MAX_OUTPUT_TOKENS = 2048


class RuntimeProfile(StrEnum):
    PLAN = "plan"
    AXIS = "axis"
    PROJECT = "project"
    PRODUCE = "produce"
    APPROVE = "approve"


COMMAND_PROFILES: dict[str, RuntimeProfile] = {
    "plan": RuntimeProfile.PLAN,
    "revise-axis": RuntimeProfile.AXIS,
    "approve-axis": RuntimeProfile.PROJECT,
    "revise-project": RuntimeProfile.PROJECT,
    "produce": RuntimeProfile.PRODUCE,
    "approve": RuntimeProfile.APPROVE,
}


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
    context_window: int = GEMINI_CONTEXT_WINDOW
    graph_max_tokens: int = GRAPH_MAX_OUTPUT_TOKENS
    axis_max_tokens: int = AXIS_MAX_OUTPUT_TOKENS
    project_max_tokens: int = PROJECT_MAX_OUTPUT_TOKENS
    research_max_tokens: int = RESEARCH_MAX_OUTPUT_TOKENS
    architecture_max_tokens: int = ARCHITECTURE_MAX_OUTPUT_TOKENS
    writing_max_tokens: int = WRITING_MAX_OUTPUT_TOKENS
    critic_max_tokens: int = CRITIC_MAX_OUTPUT_TOKENS
    editing_max_tokens: int = EDITING_MAX_OUTPUT_TOKENS


@dataclass(slots=True)
class HefestoRuntime:
    """Model-backed capabilities required by one worker invocation."""

    planning_workflow: EditorialPlanningWorkflow | None = None
    axis_planner: NarrativeAxisPlanner | None = None
    editorial_planner: EditorialPlanner | None = None
    production_workflow: EditorialProductionWorkflow | None = None
    graph_llm: LLM | None = None
    embed_model: BaseEmbedding | None = None

    def require(self, name: str) -> object:
        value = getattr(self, name)
        if value is None:
            raise RuntimeError(f"{name} is not constructed for this operation")
        return value

    def load_graph(self, directory: Path) -> ProjectGraph:
        graph_llm = self.require("graph_llm")
        embed_model = self.require("embed_model")
        return ProjectGraph.load(
            directory,
            llm=graph_llm,  # type: ignore[arg-type]
            embed_model=embed_model,  # type: ignore[arg-type]
        )


def profile_for_command(command: str) -> RuntimeProfile:
    try:
        return COMMAND_PROFILES[command]
    except KeyError as error:
        raise ValueError(f"unknown worker command: {command}") from error


def _require_stage_cap(max_tokens: int, *, stage: str) -> int:
    if max_tokens < 1:
        raise ValueError(f"{stage} max_tokens must be a positive output cap")
    if max_tokens >= PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS:
        raise ValueError(
            f"{stage} max_tokens must stay below the published model maximum "
            f"of {PUBLISHED_GEMINI_MAX_OUTPUT_TOKENS}"
        )
    return max_tokens


def build_vertex_runtime(
    settings: VertexRuntimeSettings,
    *,
    profile: RuntimeProfile,
) -> HefestoRuntime:
    vertexai_config = {"project": settings.project, "location": settings.location}

    def llm(model: str, temperature: float, max_tokens: int, *, stage: str) -> GoogleGenAI:
        return GoogleGenAI(
            model=model,
            temperature=temperature,
            max_tokens=_require_stage_cap(max_tokens, stage=stage),
            context_window=settings.context_window,
            vertexai_config=vertexai_config,
        )

    def embeddings() -> GoogleGenAIEmbedding:
        return GoogleGenAIEmbedding(
            model_name=settings.embedding_model,
            vertexai_config=vertexai_config,
        )

    if profile is RuntimeProfile.APPROVE:
        return HefestoRuntime()

    graph_llm = llm(
        settings.graph_model, 0.1, settings.graph_max_tokens, stage="graph"
    )
    embed_model = embeddings()

    if profile is RuntimeProfile.PLAN:
        axis_planner = NarrativeAxisPlanner(
            llm(settings.planning_model, 0.4, settings.axis_max_tokens, stage="axis")
        )
        return HefestoRuntime(
            planning_workflow=EditorialPlanningWorkflow(
                graph_llm=graph_llm,
                axis_planner=axis_planner,
                embed_model=embed_model,
            ),
            axis_planner=axis_planner,
            graph_llm=graph_llm,
            embed_model=embed_model,
        )

    if profile is RuntimeProfile.AXIS:
        return HefestoRuntime(
            axis_planner=NarrativeAxisPlanner(
                llm(settings.planning_model, 0.4, settings.axis_max_tokens, stage="axis")
            ),
            graph_llm=graph_llm,
            embed_model=embed_model,
        )

    if profile is RuntimeProfile.PROJECT:
        return HefestoRuntime(
            editorial_planner=EditorialPlanner(
                llm(
                    settings.planning_model,
                    0.4,
                    settings.project_max_tokens,
                    stage="project",
                )
            ),
            graph_llm=graph_llm,
            embed_model=embed_model,
        )

    return HefestoRuntime(
        production_workflow=EditorialProductionWorkflow(
            research_llm=llm(
                settings.research_model,
                0.2,
                settings.research_max_tokens,
                stage="research",
            ),
            architecture_llm=llm(
                settings.architecture_model,
                0.5,
                settings.architecture_max_tokens,
                stage="architecture",
            ),
            writing_llm=llm(
                settings.writing_model,
                0.7,
                settings.writing_max_tokens,
                stage="writing",
            ),
            critic_llm=llm(
                settings.critic_model,
                0.2,
                settings.critic_max_tokens,
                stage="critic",
            ),
            editing_llm=llm(
                settings.editing_model,
                0.5,
                settings.editing_max_tokens,
                stage="editing",
            ),
        ),
        graph_llm=graph_llm,
        embed_model=embed_model,
    )


def build_vertex_planning_workflow(
    settings: VertexRuntimeSettings,
) -> EditorialPlanningWorkflow:
    workflow = build_vertex_runtime(
        settings, profile=RuntimeProfile.PLAN
    ).planning_workflow
    if workflow is None:
        raise RuntimeError("planning workflow was not constructed")
    return workflow


def build_vertex_production_workflow(
    settings: VertexRuntimeSettings,
) -> EditorialProductionWorkflow:
    workflow = build_vertex_runtime(
        settings, profile=RuntimeProfile.PRODUCE
    ).production_workflow
    if workflow is None:
        raise RuntimeError("production workflow was not constructed")
    return workflow

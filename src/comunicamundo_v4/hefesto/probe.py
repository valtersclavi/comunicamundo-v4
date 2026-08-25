"""Atomic, serial diagnostic boundaries for Hefesto V4 ``plan``.

PropertyGraph construction and Narrative Axis generation are separate stages.
ONE operation / ONE boundary / ONE proof. Only the earliest unpaid boundary is
enabled. Later stages, including durable graph persist and Axis generation,
stay refused until that proof lands. Paid model boundaries stay refused here.

This is LlamaIndex PropertyGraphIndex, not GraphRAG.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from .worker import PlanningJobRequest, _required_environment, load_planning_request


class ProbeBoundary(StrEnum):
    REQUEST_READ = "request-read"
    PERSISTENCE = "persistence"
    EMBED_CONSTRUCT = "embed-construct"
    GRAPH_LLM_CONSTRUCT = "graph-llm-construct"
    GRAPH_INIT = "graph-init"
    GRAPH_BUILD = "graph-build"
    GRAPH_PERSIST = "graph-persist"
    AXIS_LLM_CONSTRUCT = "axis-llm-construct"
    AXIS_GENERATE = "axis-generate"


PLAN_BOUNDARIES: tuple[ProbeBoundary, ...] = (
    ProbeBoundary.REQUEST_READ,
    ProbeBoundary.PERSISTENCE,
    ProbeBoundary.EMBED_CONSTRUCT,
    ProbeBoundary.GRAPH_LLM_CONSTRUCT,
    ProbeBoundary.GRAPH_INIT,
    ProbeBoundary.GRAPH_BUILD,
    ProbeBoundary.GRAPH_PERSIST,
    ProbeBoundary.AXIS_LLM_CONSTRUCT,
    ProbeBoundary.AXIS_GENERATE,
)

PAID_BOUNDARIES = frozenset(
    {ProbeBoundary.GRAPH_BUILD, ProbeBoundary.AXIS_GENERATE}
)
ENABLED_BOUNDARIES = frozenset({ProbeBoundary.REQUEST_READ})

BOUNDARY_CONTRACT: dict[ProbeBoundary, str] = {
    ProbeBoundary.REQUEST_READ: (
        "A. Read the planning JSON and validate PlanningJobRequest. "
        "No Vertex, no persistence clients, no PropertyGraph."
    ),
    ProbeBoundary.PERSISTENCE: (
        "Construct GCP persistence/settings needed for plan. No Vertex clients."
    ),
    ProbeBoundary.EMBED_CONSTRUCT: (
        "Construct GoogleGenAIEmbedding only. No embedding request, no graph, no LLM."
    ),
    ProbeBoundary.GRAPH_LLM_CONSTRUCT: (
        "Construct the graph GoogleGenAI only. No generation, no PropertyGraph build."
    ),
    ProbeBoundary.GRAPH_INIT: (
        "Initialize PropertyGraphIndex with clients already constructed. "
        "No from_documents, no model calls."
    ),
    ProbeBoundary.GRAPH_BUILD: (
        "B. Build or restore the source-comprehension PropertyGraphIndex. "
        "First paid graph boundary. Stop before Axis. Rebuild only when the "
        "source set/version changed or no valid durable graph artifact exists."
    ),
    ProbeBoundary.GRAPH_PERSIST: (
        "C. Persist that PropertyGraph durably immediately after B succeeds, "
        "before any Narrative Axis generation. An Axis failure must never "
        "discard a successful graph."
    ),
    ProbeBoundary.AXIS_LLM_CONSTRUCT: (
        "Construct the Narrative Axis GoogleGenAI only. No generation. "
        "Axis construction is after the durable graph checkpoint."
    ),
    ProbeBoundary.AXIS_GENERATE: (
        "D. Generate the Narrative Axis from an existing durable "
        "PropertyGraph artifact. Paid. Never reconstruct the graph on Axis failure."
    ),
}


def parse_boundary(value: str) -> ProbeBoundary:
    try:
        return ProbeBoundary(value)
    except ValueError as error:
        known = ", ".join(item.value for item in PLAN_BOUNDARIES)
        raise ValueError(f"unknown probe boundary: {value}; known: {known}") from error


def summarize_planning_request(request: PlanningJobRequest) -> dict[str, Any]:
    """Compact, non-sensitive request evidence. Never includes source text."""

    return {
        "project_id": str(request.project_id),
        "theme": request.brief.theme,
        "approach": request.brief.approach,
        "duration_minutes": request.brief.duration_minutes,
        "output_language": request.brief.output_language,
        "source_count": len(request.sources),
    }


def probe_request_read(location: str, *, project: str) -> dict[str, Any]:
    request = load_planning_request(location, project=project)
    return {
        "boundary": ProbeBoundary.REQUEST_READ.value,
        "status": "pass",
        "paid": False,
        "graph_checkpointed": False,
        **summarize_planning_request(request),
    }


def run_probe(boundary: str, *, request: str | None = None) -> dict[str, Any]:
    name = parse_boundary(boundary)
    if name in PAID_BOUNDARIES:
        raise ValueError(
            f"{name.value} is a paid model boundary and is refused until unpaid "
            "startup boundaries are proven"
        )
    if name not in ENABLED_BOUNDARIES:
        index = PLAN_BOUNDARIES.index(name) + 1
        raise ValueError(
            f"{name.value} is mapped as plan boundary {index} and is refused "
            "until request-read is proven"
        )
    if not request:
        raise ValueError("request-read requires --request")
    project = _required_environment("GOOGLE_CLOUD_PROJECT")
    return probe_request_read(request, project=project)

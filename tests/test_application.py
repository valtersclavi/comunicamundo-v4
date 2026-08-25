from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.application import HefestoApplication
from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialSubject,
    FinalArticle,
    NarrativeAxis,
    NarrativeAxisStatus,
    NormalizedSource,
)
from comunicamundo_v4.hefesto.graph import ProjectGraph
from comunicamundo_v4.hefesto.persistence import (
    GraphArtifact,
    GraphArtifactFile,
    PublishedArticle,
    StoredNarrativeAxis,
)
from comunicamundo_v4.hefesto.workflow import AxisPlanningOutcome


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)


def _artifact(project_id) -> GraphArtifact:
    artifact_id = uuid4()
    prefix = f"comunicamundo-v4/projects/{project_id}/graphs/{artifact_id}"
    return GraphArtifact(
        project_id=project_id,
        artifact_id=artifact_id,
        bucket="v4-test",
        prefix=prefix,
        manifest_uri=f"gs://v4-test/{prefix}/manifest.json",
        files=[GraphArtifactFile(path="graph.json", size_bytes=2, sha256="a" * 64)],
        created_at=NOW,
    )


class AxisPlanner:
    async def propose(self, *, project_id, brief, graph_context) -> NarrativeAxis:
        return NarrativeAxis(
            project_id=project_id,
            statement=f"Como {brief.theme} pode ser narrado a partir do material já compreendido.",
        )

    async def revise(self, *, current, brief, graph_context, feedback) -> NarrativeAxis:
        return NarrativeAxis(
            project_id=current.project_id,
            parent_axis_id=current.narrative_axis_id,
            statement=f"{current.statement} {feedback}",
        )


class ProjectPlanner:
    async def build_project(self, *, project_id, brief, graph_context, narrative_axis):
        if narrative_axis.status is not NarrativeAxisStatus.APPROVED:
            raise ValueError("Editorial Project cannot be created from an unapproved Narrative Axis")
        return EditorialProject(
            project_id=project_id,
            brief=brief,
            narrative_axis_id=narrative_axis.narrative_axis_id,
            narrative_axis=narrative_axis.statement,
            working_title="Título",
            editorial_promise="Promessa editorial",
            subjects=[
                EditorialSubject(
                    title="Assunto",
                    purpose="Propósito",
                    research_questions=["Pergunta?"],
                    estimated_minutes=brief.duration_minutes,
                )
            ],
        )


class PlanningWorkflow:
    def __init__(self) -> None:
        self._axis_planner = AxisPlanner()
        self._planner = ProjectPlanner()

    async def run(self, *, start_event):
        start_event.graph_storage_path.mkdir(parents=True)
        (start_event.graph_storage_path / "graph.json").write_text(
            "{}", encoding="utf-8"
        )
        axis = await self._axis_planner.propose(
            project_id=start_event.project_id,
            brief=start_event.brief,
            graph_context="mapa",
        )
        return AxisPlanningOutcome(
            narrative_axis=axis,
            brief=start_event.brief,
            graph_context="mapa",
        )


class ProductionWorkflow:
    async def run(self, *, start_event):
        return FinalArticle(
            project_id=start_event.project.project_id,
            editorial_project_id=start_event.project.editorial_project_id,
            title=start_event.project.working_title,
            body_markdown="Texto final editado.",
            word_count=3,
            quality_score=91,
            revision_rounds=1,
        )


class Runtime:
    def __init__(self) -> None:
        self.planning_workflow = PlanningWorkflow()
        self.production_workflow = ProductionWorkflow()

    def load_graph(self, directory: Path):
        assert (directory / "graph.json").exists()
        return ProjectGraph(index=None)


class Persistence:
    def __init__(self) -> None:
        self.axes: dict = {}
        self.stored = None
        self.published = None

    def persist_axis_planning_result(self, axis, brief, graph_context, graph_directory):
        assert (graph_directory / "graph.json").exists()
        stored = StoredNarrativeAxis(
            narrative_axis=axis,
            brief=brief,
            graph_artifact=_artifact(axis.project_id),
            graph_context=graph_context,
            updated_at=NOW,
        )
        self.axes[axis.narrative_axis_id] = stored
        return stored

    def get_narrative_axis(self, narrative_axis_id):
        return self.axes.get(narrative_axis_id)

    def save_narrative_axis(self, stored):
        self.axes[stored.narrative_axis.narrative_axis_id] = stored
        return stored

    def persist_editorial_project_from_axis(self, project, stored_axis):
        self.stored = type(
            "Stored",
            (),
            {
                "editorial_project": project,
                "graph_artifact": stored_axis.graph_artifact,
            },
        )()
        stored_axis = stored_axis.model_copy(
            update={"editorial_project_id": project.editorial_project_id}
        )
        self.axes[stored_axis.narrative_axis.narrative_axis_id] = stored_axis
        return self.stored

    def get_editorial_project(self, editorial_project_id):
        return self.stored

    def save_editorial_project(self, project):
        self.stored.editorial_project = project
        return self.stored

    def restore_graph(self, stored, destination):
        destination.mkdir(parents=True)
        (destination / "graph.json").write_text("{}", encoding="utf-8")

    def publish_article(self, article):
        self.published = PublishedArticle(
            article_id=uuid4(),
            project_id=article.project_id,
            editorial_project_id=article.editorial_project_id,
            title=article.title,
            body_uri="gs://test/article.md",
            word_count=article.word_count,
            quality_score=article.quality_score,
            revision_rounds=article.revision_rounds,
            created_at=article.created_at,
        )
        return self.published


def _brief() -> EditorialBrief:
    return EditorialBrief(theme="Tema", approach="Abordagem", duration_minutes=8)


async def test_application_stops_and_resumes_across_approval() -> None:
    persistence = Persistence()
    application = HefestoApplication(runtime=Runtime(), persistence=persistence)
    stored_axis = await application.plan_narrative_axis(
        project_id=uuid4(),
        brief=_brief(),
        sources=[
            NormalizedSource(
                source_id=uuid4(),
                text="Conteúdo da fonte.",
                language="pt",
            )
        ],
    )
    stored = await application.approve_narrative_axis(
        stored_axis.narrative_axis.narrative_axis_id
    )

    with pytest.raises(ValueError, match="approved"):
        await application.produce_article(stored.editorial_project.editorial_project_id)

    approved = await application.approve_editorial_project(
        stored.editorial_project.editorial_project_id
    )
    assert approved.editorial_project.status.value == "approved"
    assert approved.editorial_project.narrative_axis_id == (
        stored_axis.narrative_axis.narrative_axis_id
    )

    publication = await application.produce_article(
        stored.editorial_project.editorial_project_id
    )

    assert publication.quality_score == 91
    assert publication.body_uri == "gs://test/article.md"


async def test_approval_is_idempotent() -> None:
    persistence = Persistence()
    application = HefestoApplication(runtime=Runtime(), persistence=persistence)
    stored_axis = await application.plan_narrative_axis(
        project_id=uuid4(),
        brief=_brief(),
        sources=[
            NormalizedSource(
                source_id=uuid4(),
                text="Conteúdo da fonte.",
                language="pt",
            )
        ],
    )
    stored = await application.approve_narrative_axis(
        stored_axis.narrative_axis.narrative_axis_id
    )

    first = await application.approve_editorial_project(
        stored.editorial_project.editorial_project_id
    )
    second = await application.approve_editorial_project(
        stored.editorial_project.editorial_project_id
    )

    assert second.editorial_project.approved_at == first.editorial_project.approved_at

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
    NormalizedSource,
)
from comunicamundo_v4.hefesto.graph import ProjectGraph
from comunicamundo_v4.hefesto.persistence import PublishedArticle


class PlanningWorkflow:
    async def run(self, *, start_event):
        start_event.graph_storage_path.mkdir(parents=True)
        (start_event.graph_storage_path / "graph.json").write_text("{}", encoding="utf-8")
        return NarrativeAxis(
            project_id=start_event.project_id,
            brief=start_event.brief,
            statement="A primeira direção editorial.",
        )


class AxisPlanner:
    async def revise(self, *, axis, feedback, graph_context):
        return axis.revise(f"{axis.statement} {feedback}")


class EditorialPlanner:
    async def build_project(self, *, axis, graph_context):
        return EditorialProject(
            project_id=axis.project_id,
            brief=axis.brief,
            narrative_axis=axis,
            working_title="Título",
            subjects=[
                EditorialSubject(
                    title="Assunto",
                    development="Desenvolvimento compacto do assunto.",
                    estimated_minutes=axis.brief.duration_minutes,
                )
            ],
        )

    async def revise_project(self, *, project, feedback, graph_context):
        return project.revise(
            working_title=project.working_title,
            subjects=[
                EditorialSubject(
                    title="Assunto revisado",
                    development=feedback,
                    estimated_minutes=project.brief.duration_minutes,
                )
            ],
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
    planning_workflow = PlanningWorkflow()
    axis_planner = AxisPlanner()
    editorial_planner = EditorialPlanner()
    production_workflow = ProductionWorkflow()

    def load_graph(self, directory: Path):
        assert (directory / "graph.json").exists()
        return ProjectGraph(index=object())


class Graph:
    async def context_for_editorial_project(self, brief):
        return "contexto"


class Persistence:
    def __init__(self) -> None:
        self.axis = None
        self.project = None
        self.published = None

    def persist_planning_result(self, axis, graph_directory):
        assert (graph_directory / "graph.json").exists()
        self.axis = type(
            "StoredAxis",
            (),
            {
                "narrative_axis": axis,
                "graph_artifact": object(),
                "editorial_project_id": None,
            },
        )()
        return self.axis

    def get_narrative_axis(self, narrative_axis_id):
        return self.axis

    def save_narrative_axis(self, axis):
        self.axis.narrative_axis = axis
        return self.axis

    def persist_editorial_project_for_axis(self, project, stored_axis):
        self.project = type(
            "StoredProject",
            (),
            {"editorial_project": project, "graph_artifact": stored_axis.graph_artifact},
        )()
        self.axis.editorial_project_id = project.editorial_project_id
        return self.project

    def get_editorial_project(self, editorial_project_id):
        return self.project

    def save_editorial_project(self, project):
        self.project.editorial_project = project
        return self.project

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


async def test_application_has_two_durable_approval_boundaries() -> None:
    persistence = Persistence()
    application = HefestoApplication(runtime=Runtime(), persistence=persistence)
    stored_axis = await application.plan_project(
        project_id=uuid4(),
        brief=_brief(),
        sources=[NormalizedSource(source_id=uuid4(), text="Conteúdo", language="pt")],
    )

    revised_axis = await application.revise_narrative_axis(
        stored_axis.narrative_axis.narrative_axis_id,
        "Sem esquecer que o adulto também está sofrendo.",
    )
    assert revised_axis.narrative_axis.version == 2

    stored_project = await application.approve_narrative_axis(
        stored_axis.narrative_axis.narrative_axis_id
    )
    assert stored_project.editorial_project.narrative_axis.status.value == "approved"
    assert stored_project.editorial_project.status.value == "proposed"

    with pytest.raises(ValueError, match="approved"):
        await application.produce_article(stored_project.editorial_project.editorial_project_id)

    revised_project = await application.revise_editorial_project(
        stored_project.editorial_project.editorial_project_id,
        "Aprofundar o que o adulto faz na prática.",
    )
    assert revised_project.editorial_project.version == 2
    assert revised_project.editorial_project.subjects[0].title == "Assunto revisado"

    approved = await application.approve_editorial_project(
        stored_project.editorial_project.editorial_project_id
    )
    assert approved.editorial_project.status.value == "approved"

    publication = await application.produce_article(
        stored_project.editorial_project.editorial_project_id
    )
    assert publication.quality_score == 91


async def test_axis_and_project_approval_are_idempotent() -> None:
    persistence = Persistence()
    application = HefestoApplication(runtime=Runtime(), persistence=persistence)
    stored_axis = await application.plan_project(
        project_id=uuid4(),
        brief=_brief(),
        sources=[NormalizedSource(source_id=uuid4(), text="Conteúdo", language="pt")],
    )
    first_project = await application.approve_narrative_axis(
        stored_axis.narrative_axis.narrative_axis_id
    )
    second_project = await application.approve_narrative_axis(
        stored_axis.narrative_axis.narrative_axis_id
    )
    assert (
        first_project.editorial_project.editorial_project_id
        == second_project.editorial_project.editorial_project_id
    )

    first = await application.approve_editorial_project(
        first_project.editorial_project.editorial_project_id
    )
    second = await application.approve_editorial_project(
        first_project.editorial_project.editorial_project_id
    )
    assert second.editorial_project.approved_at == first.editorial_project.approved_at

from pathlib import Path
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.application import HefestoApplication
from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialSubject,
    FinalArticle,
    NormalizedSource,
)
from comunicamundo_v4.hefesto.graph import ProjectGraph
from comunicamundo_v4.hefesto.persistence import PublishedArticle


class PlanningWorkflow:
    async def run(self, *, start_event):
        start_event.graph_storage_path.mkdir(parents=True)
        (start_event.graph_storage_path / "graph.json").write_text(
            "{}", encoding="utf-8"
        )
        return EditorialProject(
            project_id=start_event.project_id,
            brief=start_event.brief,
            working_title="Título",
            editorial_promise="Promessa editorial",
            subjects=[
                EditorialSubject(
                    title="Assunto",
                    purpose="Propósito",
                    research_questions=["Pergunta?"],
                    estimated_minutes=start_event.brief.duration_minutes,
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
    production_workflow = ProductionWorkflow()

    def load_graph(self, directory: Path):
        assert (directory / "graph.json").exists()
        return ProjectGraph(index=None)


class Persistence:
    def __init__(self) -> None:
        self.stored = None
        self.published = None

    def persist_planning_result(self, project, graph_directory):
        assert (graph_directory / "graph.json").exists()
        self.stored = type(
            "Stored",
            (),
            {"editorial_project": project, "graph_artifact": object()},
        )()
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
    stored = await application.plan_project(
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

    with pytest.raises(ValueError, match="approved"):
        await application.produce_article(stored.editorial_project.editorial_project_id)

    approved = await application.approve_editorial_project(
        stored.editorial_project.editorial_project_id
    )
    assert approved.editorial_project.status.value == "approved"

    publication = await application.produce_article(
        stored.editorial_project.editorial_project_id
    )

    assert publication.quality_score == 91
    assert publication.body_uri == "gs://test/article.md"


async def test_approval_is_idempotent() -> None:
    persistence = Persistence()
    application = HefestoApplication(runtime=Runtime(), persistence=persistence)
    stored = await application.plan_project(
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

    first = await application.approve_editorial_project(
        stored.editorial_project.editorial_project_id
    )
    second = await application.approve_editorial_project(
        stored.editorial_project.editorial_project_id
    )

    assert second.editorial_project.approved_at == first.editorial_project.approved_at

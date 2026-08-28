from datetime import UTC, datetime
from uuid import uuid4

import pytest
from comunicamundo_v4.hefesto.application import HefestoApplication
from comunicamundo_v4.hefesto.contracts import EditorialBrief, EditorialProject, EditorialSubject, NarrativeAxis
from comunicamundo_v4.hefesto.persistence import GraphArtifact, GraphArtifactFile, StoredNarrativeAxis


class Director:
    async def build_project(self, **kwargs):
        axis = kwargs["axis"]
        return EditorialProject(
            project_id=axis.project_id,
            narrative_axis_id=axis.narrative_axis_id,
            narrative_axis=axis.statement,
            brief=kwargs["brief"],
            working_title="Título",
            editorial_promise="Promessa",
            core_subjects=[EditorialSubject(title="Assunto", development="Desenvolvimento")],
        )


class Runtime:
    def __init__(self):
        self.director = Director()
        self.production = None


class Persistence:
    def __init__(self):
        self.axes = {}
        self.projects = {}

    def get_axis(self, axis_id):
        return self.axes.get(axis_id)

    def save_axis(self, stored):
        self.axes[stored.narrative_axis.narrative_axis_id] = stored
        return stored

    def save_project(self, stored):
        self.projects[stored.editorial_project.editorial_project_id] = stored
        return stored

    def get_project(self, project_id):
        return self.projects.get(project_id)

    def get_run(self, planning_run_id):
        return None


def _seed_axis(persistence: Persistence, *, project_id):
    axis = NarrativeAxis(project_id=project_id, statement="Eixo proposto")
    artifact = GraphArtifact(
        project_id=project_id,
        artifact_id=uuid4(),
        bucket="b",
        prefix="p",
        manifest_uri="gs://b/m",
        files=[GraphArtifactFile(path="graph.json", size_bytes=2, sha256="0" * 64)],
        created_at=datetime.now(UTC),
    )
    stored = StoredNarrativeAxis(
        narrative_axis=axis,
        brief=EditorialBrief(theme="Luto", approach="sensível", duration_minutes=8),
        graph_artifact=artifact,
        graph_context="compreensão persistida",
        updated_at=datetime.now(UTC),
    )
    persistence.axes[axis.narrative_axis_id] = stored
    return stored


async def test_approve_axis_builds_proposed_project_and_plan_no_longer_creates_it() -> None:
    persistence = Persistence()
    app = HefestoApplication(runtime=Runtime(), persistence=persistence)
    assert not hasattr(app, "plan")
    stored_axis = _seed_axis(persistence, project_id=uuid4())
    assert persistence.projects == {}
    stored_project = await app.approve_axis(stored_axis.narrative_axis.narrative_axis_id)
    assert stored_project.editorial_project.status.value == "proposed"
    assert stored_axis.narrative_axis.status.value == "proposed"


async def test_legacy_revise_axis_is_disabled() -> None:
    persistence = Persistence()
    app = HefestoApplication(runtime=Runtime(), persistence=persistence)
    stored_axis = _seed_axis(persistence, project_id=uuid4())
    with pytest.raises(ValueError, match="review-axis-feedback"):
        await app.revise_axis(stored_axis.narrative_axis.narrative_axis_id, "Inclua outro recorte.")
    assert persistence.axes[stored_axis.narrative_axis.narrative_axis_id].narrative_axis.statement == "Eixo proposto"

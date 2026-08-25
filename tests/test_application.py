from datetime import UTC, datetime
from uuid import uuid4
from comunicamundo_v4.hefesto.application import HefestoApplication
from comunicamundo_v4.hefesto.contracts import EditorialBrief, EditorialProject, EditorialSubject, NarrativeAxis
from comunicamundo_v4.hefesto.persistence import GraphArtifact, GraphArtifactFile, StoredEditorialProject, StoredNarrativeAxis


class Graph:
    async def understand(self, brief): return "compreensão nova"
    def persist(self, directory):
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "graph.json").write_text("{}")
class Director:
    async def propose_axis(self, **kwargs): return NarrativeAxis(project_id=kwargs["project_id"], statement="Eixo proposto")
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
    def __init__(self): self.director = Director(); self.production = None
    def build_graph(self, sources): return Graph()
class Persistence:
    def __init__(self): self.axes = {}; self.projects = {}
    def persist_new_axis(self, *, axis, brief, graph_context, graph_directory):
        artifact = GraphArtifact(project_id=axis.project_id, artifact_id=uuid4(), bucket="b", prefix="p", manifest_uri="gs://b/m", files=[GraphArtifactFile(path="graph.json", size_bytes=2, sha256="0"*64)], created_at=datetime.now(UTC))
        stored = StoredNarrativeAxis(narrative_axis=axis, brief=brief, graph_artifact=artifact, graph_context=graph_context, updated_at=datetime.now(UTC))
        self.axes[axis.narrative_axis_id] = stored
        return stored
    def get_axis(self, axis_id): return self.axes.get(axis_id)
    def save_axis(self, stored): self.axes[stored.narrative_axis.narrative_axis_id] = stored; return stored
    def save_project(self, stored): self.projects[stored.editorial_project.editorial_project_id] = stored; return stored
    def get_project(self, project_id): return self.projects.get(project_id)


async def test_plan_stops_at_proposed_axis_then_approval_builds_proposed_project() -> None:
    persistence = Persistence()
    app = HefestoApplication(runtime=Runtime(), persistence=persistence)
    brief = EditorialBrief(theme="Luto", approach="sensível", duration_minutes=8)
    project_id = uuid4()
    stored_axis = await app.plan(project_id=project_id, brief=brief, sources=[object()])
    assert stored_axis.narrative_axis.status.value == "proposed"
    assert persistence.projects == {}
    stored_project = await app.approve_axis(stored_axis.narrative_axis.narrative_axis_id)
    assert stored_project.editorial_project.status.value == "proposed"

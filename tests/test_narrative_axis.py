"""Product contract for the Narrative Axis on the proven be5292d runtime."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from llama_index.core.embeddings import MockEmbedding
from llama_index.core.llms import MockLLM
from pydantic import ValidationError

from comunicamundo_v4.hefesto.application import HefestoApplication
from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialProjectStatus,
    EditorialReview,
    EditorialSubject,
    NarrativeAxis,
    NarrativeAxisStatus,
    NormalizedSource,
)
from comunicamundo_v4.hefesto.persistence import (
    FirestoreEditorialRepository,
    GcpEditorialPersistence,
    GcsArtifactStore,
    GraphArtifact,
    GraphArtifactFile,
    PublishedArticle,
    StoredNarrativeAxis,
)
from comunicamundo_v4.hefesto.planner import EditorialPlanner, NarrativeAxisPlanner
from comunicamundo_v4.hefesto.production import (
    ArticleWriter,
    EditorialCritic,
    NarrativeArchitect,
)
from comunicamundo_v4.hefesto.prompts import (
    ARTICLE_DRAFT_PROMPT,
    EDITORIAL_PROJECT_PROMPT,
    EDITORIAL_REVIEW_PROMPT,
    NARRATIVE_AXIS_PROMPT,
    NARRATIVE_AXIS_REVISION_PROMPT,
    NARRATIVE_PLAN_PROMPT,
)
from comunicamundo_v4.hefesto.workflow import (
    AxisPlanningOutcome,
    EditorialPlanningWorkflow,
    run_narrative_axis_planning,
)


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
DIGEST = "a" * 64


def _brief() -> EditorialBrief:
    return EditorialBrief(
        theme="O vivenciar do luto na infância",
        approach="Texto sensível para familiares",
        duration_minutes=8,
        audience="familiares",
    )


def _source() -> NormalizedSource:
    return NormalizedSource(
        source_id=uuid4(),
        text="A comunicação honesta ajuda a criança a viver o processo de luto.",
        language="pt",
    )


def _axis(*, project_id: UUID, statement: str, parent_axis_id: UUID | None = None) -> NarrativeAxis:
    return NarrativeAxis(
        project_id=project_id,
        parent_axis_id=parent_axis_id,
        statement=statement,
    )


def _artifact(project_id: UUID) -> GraphArtifact:
    artifact_id = uuid4()
    prefix = f"comunicamundo-v4/projects/{project_id}/graphs/{artifact_id}"
    return GraphArtifact(
        project_id=project_id,
        artifact_id=artifact_id,
        bucket="v4-test",
        prefix=prefix,
        manifest_uri=f"gs://v4-test/{prefix}/manifest.json",
        files=[GraphArtifactFile(path="graph.json", size_bytes=2, sha256=DIGEST)],
        created_at=NOW,
    )


def _subject(duration_minutes: int) -> EditorialSubject:
    return EditorialSubject(
        title="A comunicação com a criança",
        purpose="Explorar a relação entre verdade e cuidado.",
        research_questions=["Como conversar sobre a morte?"],
        estimated_minutes=duration_minutes,
    )


class AxisPlanner:
    async def propose(self, *, project_id, brief, graph_context) -> NarrativeAxis:
        return _axis(
            project_id=project_id,
            statement=(
                f"O texto parte de {brief.theme} para tensionar o que o mapa "
                f"revela: {graph_context}."
            ),
        )

    async def revise(self, *, current, brief, graph_context, feedback) -> NarrativeAxis:
        return _axis(
            project_id=current.project_id,
            parent_axis_id=current.narrative_axis_id,
            statement=f"{current.statement.rstrip('.')} e {feedback.strip().rstrip('.')}.",
        )


class ProjectPlanner:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def build_project(self, *, project_id, brief, graph_context, narrative_axis):
        self.calls.append(
            {
                "project_id": project_id,
                "brief": brief,
                "graph_context": graph_context,
                "narrative_axis": narrative_axis,
            }
        )
        if narrative_axis.status is not NarrativeAxisStatus.APPROVED:
            raise ValueError("Editorial Project cannot be created from an unapproved Narrative Axis")
        return EditorialProject(
            project_id=project_id,
            brief=brief,
            working_title="Quando a infância encontra a perda",
            editorial_promise="Compreender a experiência infantil sem reduzi-la.",
            narrative_axis_id=narrative_axis.narrative_axis_id,
            narrative_axis=narrative_axis.statement,
            subjects=[_subject(brief.duration_minutes)],
        )


class PlanningWorkflow:
    def __init__(self) -> None:
        self.graph_builds = 0
        self._axis_planner = AxisPlanner()
        self._planner = ProjectPlanner()

    async def run(self, *, start_event):
        self.graph_builds += 1
        start_event.graph_storage_path.mkdir(parents=True)
        (start_event.graph_storage_path / "graph.json").write_text(
            "{}", encoding="utf-8"
        )
        graph_context = (
            f"mapa:{start_event.brief.theme}|adulto-também-sofre"
        )
        axis = await self._axis_planner.propose(
            project_id=start_event.project_id,
            brief=start_event.brief,
            graph_context=graph_context,
        )
        return AxisPlanningOutcome(
            narrative_axis=axis,
            brief=start_event.brief,
            graph_context=graph_context,
        )


class ProductionWorkflow:
    async def run(self, *, start_event):
        return type("Article", (), {})()


class Runtime:
    def __init__(self) -> None:
        self.planning_workflow = PlanningWorkflow()
        self.production_workflow = ProductionWorkflow()

    def load_graph(self, directory: Path):
        raise AssertionError("axis planning and approval must not rebuild or reload the graph")


class Persistence:
    def __init__(self) -> None:
        self.axes: dict[UUID, StoredNarrativeAxis] = {}
        self.projects = None
        self.graph_uploads = 0
        self.published = None

    def persist_axis_planning_result(self, axis, brief, graph_context, graph_directory):
        assert (graph_directory / "graph.json").exists()
        self.graph_uploads += 1
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

    def save_narrative_axis(self, stored: StoredNarrativeAxis):
        self.axes[stored.narrative_axis.narrative_axis_id] = stored
        return stored

    def persist_editorial_project_from_axis(self, project, stored_axis):
        self.projects = type(
            "StoredProject",
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
        return self.projects

    def get_editorial_project(self, editorial_project_id):
        return self.projects

    def save_editorial_project(self, project):
        self.projects.editorial_project = project
        return self.projects

    def restore_graph(self, stored, destination):
        raise AssertionError("axis revision and approval must reuse the persisted graph context")

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


def _application() -> tuple[HefestoApplication, Runtime, Persistence]:
    runtime = Runtime()
    persistence = Persistence()
    return HefestoApplication(runtime=runtime, persistence=persistence), runtime, persistence


# --- 1. plan stops at a proposed Narrative Axis, not an Editorial Project ---


async def test_plan_stops_at_proposed_narrative_axis() -> None:
    application, runtime, persistence = _application()

    stored = await application.plan_narrative_axis(
        project_id=uuid4(),
        brief=_brief(),
        sources=[_source()],
    )

    assert isinstance(stored, StoredNarrativeAxis)
    assert stored.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert stored.narrative_axis.approved_at is None
    assert persistence.projects is None
    assert runtime.planning_workflow.graph_builds == 1
    assert persistence.graph_uploads == 1


# --- 2. proposed Axis is derived from brief + graph context ---


async def test_proposed_axis_is_derived_from_brief_and_graph_context() -> None:
    application, _, stored_state = _application()
    brief = _brief()

    stored = await application.plan_narrative_axis(
        project_id=uuid4(),
        brief=brief,
        sources=[_source()],
    )

    assert brief.theme in stored.narrative_axis.statement
    assert "adulto-também-sofre" in stored.narrative_axis.statement
    assert stored.graph_context.endswith("adulto-também-sofre")


class DerivingLlm:
    def __init__(self) -> None:
        self.kwargs: dict | None = None

    async def astructured_predict(self, output_cls, prompt, **kwargs):
        self.kwargs = kwargs
        theme = kwargs["theme"]
        graph_context = kwargs["graph_context"]
        return output_cls(
            statement=(
                f"Como {theme} pode ser contado sem esconder que {graph_context}?"
            )
        )


async def test_axis_planner_derives_statement_from_brief_and_graph_context() -> None:
    llm = DerivingLlm()
    planner = NarrativeAxisPlanner(llm)
    brief = _brief()

    axis = await planner.propose(
        project_id=uuid4(),
        brief=brief,
        graph_context="o adulto também sofre",
    )

    assert llm.kwargs is not None
    assert llm.kwargs["theme"] == brief.theme
    assert llm.kwargs["approach"] == brief.approach
    assert llm.kwargs["graph_context"] == "o adulto também sofre"
    assert brief.theme in axis.statement
    assert "o adulto também sofre" in axis.statement
    assert axis.status is NarrativeAxisStatus.PROPOSED
    assert axis.approved_at is None
    assert axis.parent_axis_id is None


# --- 3. Axis is not approved automatically ---


def test_narrative_axis_is_proposed_not_auto_approved() -> None:
    axis = _axis(
        project_id=uuid4(),
        statement="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
    )

    assert axis.status is NarrativeAxisStatus.PROPOSED
    assert axis.approved_at is None


# --- 4. natural-language revision produces a new proposed version ---


async def test_revision_creates_new_proposed_axis_and_preserves_parent_identity() -> None:
    application, runtime, persistence = _application()
    original = await application.plan_narrative_axis(
        project_id=uuid4(),
        brief=_brief(),
        sources=[_source()],
    )

    revised = await application.revise_narrative_axis(
        original.narrative_axis.narrative_axis_id,
        "inclua que o adulto também sofre",
    )

    assert revised.narrative_axis.narrative_axis_id != original.narrative_axis.narrative_axis_id
    assert revised.narrative_axis.parent_axis_id == original.narrative_axis.narrative_axis_id
    assert revised.narrative_axis.project_id == original.narrative_axis.project_id
    assert revised.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert "adulto também sofre" in revised.narrative_axis.statement
    assert revised.graph_artifact == original.graph_artifact
    assert revised.graph_context == original.graph_context
    assert persistence.graph_uploads == 1
    assert runtime.planning_workflow.graph_builds == 1

    parent = persistence.get_narrative_axis(original.narrative_axis.narrative_axis_id)
    assert parent is not None
    assert parent.narrative_axis.status is NarrativeAxisStatus.SUPERSEDED


# --- 5. approved Axis is immutable ---


async def test_approved_axis_is_immutable() -> None:
    application, _, _ = _application()
    stored = await application.plan_narrative_axis(
        project_id=uuid4(),
        brief=_brief(),
        sources=[_source()],
    )
    approved_project = await application.approve_narrative_axis(
        stored.narrative_axis.narrative_axis_id
    )
    approved_axis = stored.narrative_axis.approve()

    assert approved_project.editorial_project.narrative_axis_id == stored.narrative_axis.narrative_axis_id
    with pytest.raises((ValidationError, TypeError, ValueError)):
        approved_axis.statement = "outro centro editorial"
    with pytest.raises(ValueError, match="immutable"):
        await application.revise_narrative_axis(
            stored.narrative_axis.narrative_axis_id,
            "mude o eixo",
        )


# --- 6. Editorial Project cannot be created from an unapproved Axis ---


async def test_editorial_project_cannot_be_created_from_unapproved_axis() -> None:
    planner = ProjectPlanner()
    axis = _axis(
        project_id=uuid4(),
        statement="O luto infantil exige ver também o sofrimento adulto.",
    )

    with pytest.raises(ValueError, match="unapproved"):
        await planner.build_project(
            project_id=axis.project_id,
            brief=_brief(),
            graph_context="mapa",
            narrative_axis=axis,
        )

    real_planner = EditorialPlanner(DerivingProjectLlm())
    with pytest.raises(ValueError, match="unapproved"):
        await real_planner.build_project(
            project_id=axis.project_id,
            brief=_brief(),
            graph_context="mapa",
            narrative_axis=axis,
        )


class DerivingProjectLlm:
    async def astructured_predict(self, output_cls, prompt, **kwargs):
        raise AssertionError("unapproved axis must not reach project generation")


# --- 7. Project generation reuses the same graph artifact ---


async def test_approve_axis_reuses_persisted_graph_and_does_not_rebuild() -> None:
    application, runtime, persistence = _application()
    stored = await application.plan_narrative_axis(
        project_id=uuid4(),
        brief=_brief(),
        sources=[_source()],
    )
    original_artifact = stored.graph_artifact

    project = await application.approve_narrative_axis(
        stored.narrative_axis.narrative_axis_id
    )

    assert project.graph_artifact == original_artifact
    assert persistence.graph_uploads == 1
    assert runtime.planning_workflow.graph_builds == 1
    assert runtime.planning_workflow._planner.calls[0]["graph_context"] == stored.graph_context
    assert runtime.planning_workflow._planner.calls[0]["narrative_axis"].status is (
        NarrativeAxisStatus.APPROVED
    )


class FakeBlob:
    def __init__(self, objects: dict[str, bytes], name: str) -> None:
        self.objects = objects
        self.name = name

    def upload_from_filename(self, filename: str, content_type=None) -> None:
        self.objects[self.name] = Path(filename).read_bytes()

    def upload_from_string(self, data: bytes, content_type=None) -> None:
        self.objects[self.name] = data

    def download_as_bytes(self) -> bytes:
        return self.objects[self.name]


class FakeBucket:
    def __init__(self, objects: dict[str, bytes]) -> None:
        self.objects = objects

    def blob(self, name: str) -> FakeBlob:
        return FakeBlob(self.objects, name)


class FakeStorageClient:
    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}

    def bucket(self, name: str) -> FakeBucket:
        return FakeBucket(self.objects)


class FakeSnapshot:
    def __init__(self, data) -> None:
        self._data = data
        self.exists = data is not None

    def to_dict(self):
        return self._data


class FakeDocument:
    def __init__(self, documents: dict[str, dict], key: str) -> None:
        self.documents = documents
        self.key = key

    def set(self, data: dict) -> None:
        self.documents[self.key] = data

    def get(self) -> FakeSnapshot:
        return FakeSnapshot(self.documents.get(self.key))


class FakeCollection:
    def __init__(self, documents: dict[str, dict], name: str) -> None:
        self.documents = documents
        self.name = name

    def document(self, identifier: str) -> FakeDocument:
        return FakeDocument(self.documents, f"{self.name}/{identifier}")


class FakeFirestoreClient:
    def __init__(self) -> None:
        self.documents: dict[str, dict] = {}

    def collection(self, name: str) -> FakeCollection:
        return FakeCollection(self.documents, name)


def _persistence(storage_client, firestore_client) -> GcpEditorialPersistence:
    identifiers = iter([UUID("00000000-0000-0000-0000-000000000001")])
    return GcpEditorialPersistence(
        artifacts=GcsArtifactStore(
            storage_client,
            bucket_name="v4-test",
            id_factory=lambda: next(identifiers),
            now=lambda: NOW,
        ),
        repository=FirestoreEditorialRepository(
            firestore_client,
            now=lambda: NOW,
        ),
    )


def test_persistence_associates_existing_graph_artifact_with_axis_then_project(
    tmp_path,
) -> None:
    storage_client = FakeStorageClient()
    firestore_client = FakeFirestoreClient()
    persistence = _persistence(storage_client, firestore_client)
    graph_directory = tmp_path / "graph"
    graph_directory.mkdir()
    (graph_directory / "graph.json").write_text("{}", encoding="utf-8")
    project_id = uuid4()
    axis = _axis(
        project_id=project_id,
        statement="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
    )

    stored_axis = persistence.persist_axis_planning_result(
        axis,
        _brief(),
        "mapa persistido",
        graph_directory,
    )
    graph_objects_after_plan = {
        name for name in storage_client.objects if "/graphs/" in name
    }

    approved = axis.approve()
    project = EditorialProject(
        project_id=project_id,
        brief=_brief(),
        working_title="Quando a infância encontra a perda",
        editorial_promise="Compreender a experiência infantil do luto.",
        narrative_axis_id=approved.narrative_axis_id,
        narrative_axis=approved.statement,
        subjects=[_subject(8)],
    )
    stored_project = persistence.persist_editorial_project_from_axis(
        project,
        stored_axis.model_copy(update={"narrative_axis": approved}),
    )

    assert stored_project.graph_artifact == stored_axis.graph_artifact
    assert {
        name for name in storage_client.objects if "/graphs/" in name
    } == graph_objects_after_plan


# --- 8. Editorial Project carries the approved Axis; prompt receives it ---


async def test_editorial_project_carries_approved_axis_and_prompt_receives_it() -> None:
    application, runtime, _ = _application()
    stored = await application.plan_narrative_axis(
        project_id=uuid4(),
        brief=_brief(),
        sources=[_source()],
    )

    project = await application.approve_narrative_axis(
        stored.narrative_axis.narrative_axis_id
    )
    approved_axis = runtime.planning_workflow._planner.calls[0]["narrative_axis"]

    assert project.editorial_project.narrative_axis_id == stored.narrative_axis.narrative_axis_id
    assert project.editorial_project.narrative_axis == approved_axis.statement
    assert approved_axis.status is NarrativeAxisStatus.APPROVED
    assert project.editorial_project.status is EditorialProjectStatus.PROPOSED
    assert project.editorial_project.approved_at is None

    rendered = EDITORIAL_PROJECT_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público geral",
        graph_context="conteúdo",
        narrative_axis=approved_axis.statement,
    )
    assert approved_axis.statement in rendered
    assert "Eixo Narrativo" in rendered


# --- 9. downstream architecture/writing/review receive the approved Axis ---


def test_downstream_prompts_receive_the_approved_axis() -> None:
    axis = "O luto infantil só se compreende quando o sofrimento adulto também entra em cena."
    rendered_plan = NARRATIVE_PLAN_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público",
        editorial_project="{}",
        narrative_axis=axis,
        subject_research="[]",
    )
    rendered_draft = ARTICLE_DRAFT_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público",
        editorial_project="{}",
        narrative_axis=axis,
        narrative_plan="{}",
        subject_research="[]",
    )
    rendered_review = EDITORIAL_REVIEW_PROMPT.format(
        brief="{}",
        narrative_axis=axis,
        editorial_project="{}",
        draft="texto",
    )
    for rendered in (rendered_plan, rendered_draft, rendered_review):
        assert axis in rendered
        assert "Eixo Narrativo" in rendered


async def test_architect_writer_and_critic_are_given_the_approved_axis() -> None:
    axis_statement = (
        "O luto infantil só se compreende quando o sofrimento adulto também entra em cena."
    )
    subject = _subject(8)
    project = EditorialProject(
        project_id=uuid4(),
        brief=_brief(),
        working_title="Quando a infância encontra a perda",
        editorial_promise="Compreender para acolher.",
        narrative_axis_id=uuid4(),
        narrative_axis=axis_statement,
        subjects=[subject],
    ).approve()

    from comunicamundo_v4.hefesto.contracts import NarrativeBeat, NarrativePlan, SubjectResearch

    class ArchitectLlm:
        def __init__(self) -> None:
            self.kwargs = None

        async def astructured_predict(self, output_cls, prompt, **kwargs):
            self.kwargs = kwargs
            return NarrativePlan(
                title=project.working_title,
                opening_move="Começar pela pergunta da criança.",
                central_thesis="Acolher exige verdade compreensível.",
                beats=[
                    NarrativeBeat(
                        subject_id=subject.subject_id,
                        narrative_role="Desenvolver a tensão central.",
                        transition="Da pergunta infantil para a resposta adulta.",
                    )
                ],
                closing_move="Retornar à pergunta com uma resposta possível.",
            )

    class WriterLlm:
        def __init__(self) -> None:
            self.kwargs = None

        async def apredict(self, prompt, **kwargs):
            self.kwargs = kwargs
            return "Uma criança pergunta o que os adultos evitam."

    class CriticLlm:
        def __init__(self) -> None:
            self.kwargs = None

        async def astructured_predict(self, output_cls, prompt, **kwargs):
            self.kwargs = kwargs
            return EditorialReview(ready_to_publish=True, quality_score=91)

    architect_llm = ArchitectLlm()
    writer_llm = WriterLlm()
    critic_llm = CriticLlm()
    research = [
        SubjectResearch(
            subject_id=subject.subject_id,
            synthesis="A comunicação adulta participa da elaboração do luto.",
        )
    ]

    plan = await NarrativeArchitect(architect_llm).plan(project=project, research=research)
    await ArticleWriter(writer_llm).draft(
        project=project,
        research=research,
        narrative_plan=plan,
    )
    await EditorialCritic(critic_llm).review(project=project, draft="texto")

    assert architect_llm.kwargs["narrative_axis"] == axis_statement
    assert writer_llm.kwargs["narrative_axis"] == axis_statement
    assert critic_llm.kwargs["narrative_axis"] == axis_statement


# --- proven graph path still runs once during plan ---


class DeterministicAxisPlanner:
    async def propose(self, *, project_id, brief, graph_context) -> NarrativeAxis:
        assert graph_context
        return _axis(
            project_id=project_id,
            statement=(
                f"Como {brief.theme} pode ser narrado a partir da compreensão "
                "já recuperada do material."
            ),
        )


async def test_workflow_builds_graph_once_and_stops_at_proposed_axis(tmp_path) -> None:
    workflow = EditorialPlanningWorkflow(
        graph_llm=MockLLM(max_tokens=64),
        planning_llm=MockLLM(max_tokens=64),
        embed_model=MockEmbedding(embed_dim=8),
    )
    workflow._axis_planner = DeterministicAxisPlanner()

    result = await run_narrative_axis_planning(
        workflow,
        project_id=uuid4(),
        brief=_brief(),
        sources=[_source()],
        graph_storage_path=tmp_path / "graph",
    )

    assert isinstance(result, AxisPlanningOutcome)
    assert result.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert result.narrative_axis.approved_at is None
    assert _brief().theme in result.narrative_axis.statement
    assert result.graph_context
    assert (tmp_path / "graph" / "property_graph_store.json").exists()


def test_axis_prompts_keep_the_visible_axis_compact() -> None:
    proposed = NARRATIVE_AXIS_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público",
        graph_context="mapa",
    )
    revised = NARRATIVE_AXIS_REVISION_PROMPT.format(
        theme="Tema",
        approach="Abordagem",
        duration_minutes=8,
        output_language="pt-BR",
        audience="público",
        graph_context="mapa",
        current_axis="Eixo atual.",
        feedback="inclua que o adulto também sofre",
    )

    for rendered in (proposed, revised):
        assert "1 ou 2 frases" in rendered or "1–2 frases" in rendered
        assert "outline" in rendered.lower()
        assert "não mencione grafo" in rendered.lower() or "nao mencione grafo" in rendered.lower()

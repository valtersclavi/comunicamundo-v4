from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialSubject,
    FinalArticle,
)
from comunicamundo_v4.hefesto.persistence import (
    FirestoreEditorialRepository,
    GcpEditorialPersistence,
    GcsArtifactStore,
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


NOW = datetime(2026, 8, 22, 12, 0, tzinfo=UTC)
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000001")
ARTICLE_ID = UUID("00000000-0000-0000-0000-000000000002")


def _project() -> EditorialProject:
    return EditorialProject(
        project_id=uuid4(),
        brief=EditorialBrief(
            theme="O vivenciar do luto na infância",
            approach="Texto sensível para familiares",
            duration_minutes=8,
        ),
        narrative_axis_id=uuid4(),
        narrative_axis="O luto infantil só se compreende quando o sofrimento adulto também entra em cena.",
        working_title="Quando a infância encontra a perda",
        editorial_promise="Compreender a experiência infantil do luto.",
        subjects=[
            EditorialSubject(
                title="A comunicação com a criança",
                purpose="Explorar a relação entre verdade e cuidado.",
                research_questions=["Como conversar sobre a morte?"],
                estimated_minutes=8,
            )
        ],
    )


def _persistence(storage_client, firestore_client) -> GcpEditorialPersistence:
    identifiers = iter([ARTIFACT_ID, ARTICLE_ID])
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


def test_graph_round_trip_crosses_the_approval_boundary(tmp_path) -> None:
    storage_client = FakeStorageClient()
    persistence = _persistence(storage_client, FakeFirestoreClient())
    graph_directory = tmp_path / "graph"
    graph_directory.mkdir()
    (graph_directory / "property_graph_store.json").write_text(
        '{"nodes": ["a", "b"]}', encoding="utf-8"
    )
    nested = graph_directory / "index"
    nested.mkdir()
    (nested / "vector_store.json").write_text('{"vectors": []}', encoding="utf-8")

    stored = persistence.persist_planning_result(_project(), graph_directory)
    restored = tmp_path / "restored"
    persistence.restore_graph(stored, restored)

    assert (restored / "property_graph_store.json").read_bytes() == (
        graph_directory / "property_graph_store.json"
    ).read_bytes()
    assert (restored / "index" / "vector_store.json").read_bytes() == (
        nested / "vector_store.json"
    ).read_bytes()
    assert stored.graph_artifact.manifest_uri.endswith("/manifest.json")


def test_corrupted_graph_is_rejected_before_files_are_written(tmp_path) -> None:
    storage_client = FakeStorageClient()
    persistence = _persistence(storage_client, FakeFirestoreClient())
    graph_directory = tmp_path / "graph"
    graph_directory.mkdir()
    (graph_directory / "graph.json").write_text("original", encoding="utf-8")
    stored = persistence.persist_planning_result(_project(), graph_directory)
    object_name = f"{stored.graph_artifact.prefix}/files/graph.json"
    storage_client.objects[object_name] = b"corrupted"
    restored = tmp_path / "restored"

    with pytest.raises(ValueError, match="mismatch"):
        persistence.restore_graph(stored, restored)

    assert not any(restored.iterdir())


def test_approval_updates_firestore_without_replacing_the_graph(tmp_path) -> None:
    storage_client = FakeStorageClient()
    firestore_client = FakeFirestoreClient()
    persistence = _persistence(storage_client, firestore_client)
    graph_directory = tmp_path / "graph"
    graph_directory.mkdir()
    (graph_directory / "graph.json").write_text("{}", encoding="utf-8")
    proposed = _project()
    original = persistence.persist_planning_result(proposed, graph_directory)

    approved = persistence.save_editorial_project(proposed.approve())
    reloaded = persistence.repository.get_editorial_project(
        proposed.editorial_project_id
    )

    assert approved.graph_artifact == original.graph_artifact
    assert reloaded is not None
    assert reloaded.editorial_project.approved_at is not None


def test_article_body_lives_in_storage_not_firestore(tmp_path) -> None:
    storage_client = FakeStorageClient()
    firestore_client = FakeFirestoreClient()
    persistence = _persistence(storage_client, firestore_client)
    project = _project().approve()
    article = FinalArticle(
        project_id=project.project_id,
        editorial_project_id=project.editorial_project_id,
        title=project.working_title,
        body_markdown="# Texto final\n\nUm texto cuidadosamente editado.",
        word_count=6,
        quality_score=94,
        revision_rounds=1,
        created_at=NOW,
    )

    publication = persistence.publish_article(article)

    firestore_document = firestore_client.documents[
        f"hefesto_v4_articles/{publication.article_id}"
    ]
    object_name = publication.body_uri.removeprefix("gs://v4-test/")
    assert "body_markdown" not in firestore_document
    assert firestore_document["body_uri"] == publication.body_uri
    assert storage_client.objects[object_name].decode("utf-8") == article.body_markdown

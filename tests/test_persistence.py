from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief,
    EditorialProject,
    EditorialSubject,
    FinalArticle,
    NarrativeAxis,
)
from comunicamundo_v4.hefesto.persistence import FirestoreEditorialRepository, GcpEditorialPersistence, GcsArtifactStore


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
    def __init__(self, objects): self.objects = objects
    def blob(self, name): return FakeBlob(self.objects, name)


class FakeStorageClient:
    def __init__(self): self.objects = {}
    def bucket(self, name): return FakeBucket(self.objects)


class FakeSnapshot:
    def __init__(self, data): self._data, self.exists = data, data is not None
    def to_dict(self): return self._data


class FakeDocument:
    def __init__(self, documents, key): self.documents, self.key = documents, key
    def set(self, data): self.documents[self.key] = data
    def get(self): return FakeSnapshot(self.documents.get(self.key))


class FakeCollection:
    def __init__(self, documents, name): self.documents, self.name = documents, name
    def document(self, identifier): return FakeDocument(self.documents, f"{self.name}/{identifier}")


class FakeFirestoreClient:
    def __init__(self): self.documents = {}
    def collection(self, name): return FakeCollection(self.documents, name)


NOW = datetime(2026, 8, 24, 18, 0, tzinfo=UTC)
ARTIFACT_ID = UUID("00000000-0000-0000-0000-000000000001")
ARTICLE_ID = UUID("00000000-0000-0000-0000-000000000002")


def _brief():
    return EditorialBrief(theme="O vivenciar do luto na infância", approach="Texto sensível para familiares", duration_minutes=8)


def _axis(project_id=None):
    return NarrativeAxis(
        project_id=project_id or uuid4(),
        brief=_brief(),
        statement="Acolher exige verdade e segurança sem apagar o sofrimento do adulto.",
    )


def _project(axis):
    approved_axis = axis if axis.status.value == "approved" else axis.approve()
    return EditorialProject(
        project_id=approved_axis.project_id,
        brief=approved_axis.brief,
        narrative_axis=approved_axis,
        working_title="Quando a infância encontra a perda",
        subjects=[EditorialSubject(title="A comunicação com a criança", development="Transformar compreensão em cuidado concreto.", estimated_minutes=8)],
    )


def _persistence(storage_client, firestore_client):
    identifiers = iter([ARTIFACT_ID, ARTICLE_ID])
    return GcpEditorialPersistence(
        artifacts=GcsArtifactStore(storage_client, bucket_name="v4-test", id_factory=lambda: next(identifiers), now=lambda: NOW),
        repository=FirestoreEditorialRepository(firestore_client, now=lambda: NOW),
    )


def _graph(tmp_path):
    directory = tmp_path / "graph"
    directory.mkdir()
    (directory / "graph.json").write_text("{}", encoding="utf-8")
    return directory


def test_graph_is_uploaded_once_before_both_approval_boundaries(tmp_path) -> None:
    storage_client = FakeStorageClient()
    firestore_client = FakeFirestoreClient()
    persistence = _persistence(storage_client, firestore_client)
    axis = _axis()
    stored_axis = persistence.persist_planning_result(axis, _graph(tmp_path))
    approved_axis = persistence.save_narrative_axis(axis.approve())
    project = _project(approved_axis.narrative_axis)
    stored_project = persistence.persist_editorial_project_for_axis(project, approved_axis)

    assert stored_axis.graph_artifact == stored_project.graph_artifact
    reloaded_axis = persistence.get_narrative_axis(axis.narrative_axis_id)
    assert reloaded_axis.editorial_project_id == project.editorial_project_id


def test_graph_round_trip_crosses_axis_boundary(tmp_path) -> None:
    storage_client = FakeStorageClient()
    persistence = _persistence(storage_client, FakeFirestoreClient())
    stored = persistence.persist_planning_result(_axis(), _graph(tmp_path))
    restored = tmp_path / "restored"
    persistence.restore_graph(stored, restored)
    assert (restored / "graph.json").read_text() == "{}"
    assert stored.graph_artifact.manifest_uri.endswith("/manifest.json")


def test_corrupted_graph_is_rejected_before_files_are_written(tmp_path) -> None:
    storage_client = FakeStorageClient()
    persistence = _persistence(storage_client, FakeFirestoreClient())
    stored = persistence.persist_planning_result(_axis(), _graph(tmp_path))
    object_name = f"{stored.graph_artifact.prefix}/files/graph.json"
    storage_client.objects[object_name] = b"corrupted"
    restored = tmp_path / "restored"
    with pytest.raises(ValueError, match="mismatch"):
        persistence.restore_graph(stored, restored)
    assert not any(restored.iterdir())


def test_project_approval_updates_firestore_without_replacing_graph(tmp_path) -> None:
    storage_client = FakeStorageClient()
    firestore_client = FakeFirestoreClient()
    persistence = _persistence(storage_client, firestore_client)
    axis = _axis()
    persistence.persist_planning_result(axis, _graph(tmp_path))
    approved_axis = persistence.save_narrative_axis(axis.approve())
    project = _project(approved_axis.narrative_axis)
    original = persistence.persist_editorial_project_for_axis(project, approved_axis)
    approved = persistence.save_editorial_project(project.approve())
    assert approved.graph_artifact == original.graph_artifact
    assert persistence.get_editorial_project(project.editorial_project_id).editorial_project.approved_at is not None


def test_article_body_lives_in_storage_not_firestore(tmp_path) -> None:
    storage_client = FakeStorageClient()
    firestore_client = FakeFirestoreClient()
    persistence = _persistence(storage_client, firestore_client)
    axis = _axis().approve()
    project = _project(axis).approve()
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
    firestore_document = firestore_client.documents[f"hefesto_v4_articles/{publication.article_id}"]
    object_name = publication.body_uri.removeprefix("gs://v4-test/")
    assert "body_markdown" not in firestore_document
    assert storage_client.objects[object_name].decode("utf-8") == article.body_markdown

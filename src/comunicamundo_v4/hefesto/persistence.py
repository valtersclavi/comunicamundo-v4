"""Durable GCP boundary for the human approval split in Hefesto V4."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import mimetypes
from pathlib import Path, PurePosixPath
from typing import Any
from uuid import UUID, uuid4

from google.cloud import firestore, storage
from pydantic import Field

from .contracts import EditorialProject, FinalArticle, StrictModel


def _utc_now() -> datetime:
    return datetime.now(UTC)


class GraphArtifactFile(StrictModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GraphArtifact(StrictModel):
    project_id: UUID
    artifact_id: UUID
    bucket: str = Field(min_length=1)
    prefix: str = Field(min_length=1)
    manifest_uri: str = Field(pattern=r"^gs://")
    files: list[GraphArtifactFile] = Field(min_length=1)
    created_at: datetime


class StoredEditorialProject(StrictModel):
    editorial_project: EditorialProject
    graph_artifact: GraphArtifact
    updated_at: datetime


class PublishedArticle(StrictModel):
    article_id: UUID
    project_id: UUID
    editorial_project_id: UUID
    title: str = Field(min_length=1)
    body_uri: str = Field(pattern=r"^gs://")
    word_count: int = Field(ge=1)
    quality_score: int = Field(ge=0, le=100)
    revision_rounds: int = Field(ge=0)
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GcpPersistenceSettings:
    project: str
    bucket: str
    root_prefix: str = "comunicamundo-v4"
    editorial_projects_collection: str = "hefesto_v4_editorial_projects"
    articles_collection: str = "hefesto_v4_articles"


class GcsArtifactStore:
    """Stores immutable graph artifacts and article bodies in Cloud Storage."""

    def __init__(
        self,
        client: Any,
        *,
        bucket_name: str,
        root_prefix: str = "comunicamundo-v4",
        id_factory: Callable[[], UUID] = uuid4,
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        prefix = root_prefix.strip("/")
        if not prefix:
            raise ValueError("root_prefix cannot be empty")
        self._bucket = client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._root_prefix = prefix
        self._id_factory = id_factory
        self._now = now

    def upload_graph(self, project_id: UUID, directory: Path) -> GraphArtifact:
        directory = directory.resolve()
        if not directory.is_dir():
            raise ValueError(f"graph directory does not exist: {directory}")

        local_files = sorted(path for path in directory.rglob("*") if path.is_file())
        if not local_files:
            raise ValueError("graph directory contains no files")

        artifact_id = self._id_factory()
        prefix = (
            f"{self._root_prefix}/projects/{project_id}/graphs/{artifact_id}"
        )
        files: list[GraphArtifactFile] = []
        for local_path in local_files:
            relative_path = local_path.relative_to(directory).as_posix()
            _validate_artifact_path(relative_path)
            digest = _sha256_file(local_path)
            file_record = GraphArtifactFile(
                path=relative_path,
                size_bytes=local_path.stat().st_size,
                sha256=digest,
            )
            blob = self._bucket.blob(f"{prefix}/files/{relative_path}")
            content_type = mimetypes.guess_type(relative_path)[0]
            blob.upload_from_filename(str(local_path), content_type=content_type)
            files.append(file_record)

        created_at = self._now()
        manifest = {
            "schema_version": 1,
            "project_id": str(project_id),
            "artifact_id": str(artifact_id),
            "created_at": created_at.isoformat(),
            "files": [item.model_dump(mode="json") for item in files],
        }
        manifest_name = f"{prefix}/manifest.json"
        self._bucket.blob(manifest_name).upload_from_string(
            json.dumps(manifest, ensure_ascii=False, sort_keys=True).encode("utf-8"),
            content_type="application/json",
        )
        return GraphArtifact(
            project_id=project_id,
            artifact_id=artifact_id,
            bucket=self._bucket_name,
            prefix=prefix,
            manifest_uri=f"gs://{self._bucket_name}/{manifest_name}",
            files=files,
            created_at=created_at,
        )

    def restore_graph(self, artifact: GraphArtifact, destination: Path) -> None:
        if artifact.bucket != self._bucket_name:
            raise ValueError("graph artifact belongs to a different bucket")

        manifest_name = f"{artifact.prefix}/manifest.json"
        manifest_bytes = self._bucket.blob(manifest_name).download_as_bytes()
        manifest = json.loads(manifest_bytes.decode("utf-8"))
        _validate_manifest(manifest, artifact)

        destination = destination.resolve()
        if destination.exists() and any(destination.iterdir()):
            raise ValueError("graph restore destination must be empty")
        destination.mkdir(parents=True, exist_ok=True)

        downloads: list[tuple[GraphArtifactFile, bytes]] = []
        for file_record in artifact.files:
            _validate_artifact_path(file_record.path)
            data = self._bucket.blob(
                f"{artifact.prefix}/files/{file_record.path}"
            ).download_as_bytes()
            if len(data) != file_record.size_bytes:
                raise ValueError(f"graph artifact size mismatch: {file_record.path}")
            if sha256(data).hexdigest() != file_record.sha256:
                raise ValueError(f"graph artifact hash mismatch: {file_record.path}")
            downloads.append((file_record, data))

        for file_record, data in downloads:
            target = destination / PurePosixPath(file_record.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def upload_article(self, article: FinalArticle) -> PublishedArticle:
        article_id = self._id_factory()
        name = (
            f"{self._root_prefix}/projects/{article.project_id}/articles/"
            f"{article_id}.md"
        )
        self._bucket.blob(name).upload_from_string(
            article.body_markdown.encode("utf-8"),
            content_type="text/markdown; charset=utf-8",
        )
        return PublishedArticle(
            article_id=article_id,
            project_id=article.project_id,
            editorial_project_id=article.editorial_project_id,
            title=article.title,
            body_uri=f"gs://{self._bucket_name}/{name}",
            word_count=article.word_count,
            quality_score=article.quality_score,
            revision_rounds=article.revision_rounds,
            created_at=article.created_at,
        )


class FirestoreEditorialRepository:
    """Stores compact workflow state, never graph files or article bodies."""

    def __init__(
        self,
        client: Any,
        *,
        editorial_projects_collection: str = "hefesto_v4_editorial_projects",
        articles_collection: str = "hefesto_v4_articles",
        now: Callable[[], datetime] = _utc_now,
    ) -> None:
        self._client = client
        self._editorial_projects_collection = editorial_projects_collection
        self._articles_collection = articles_collection
        self._now = now

    def save_editorial_project(
        self,
        project: EditorialProject,
        graph_artifact: GraphArtifact,
    ) -> StoredEditorialProject:
        if project.project_id != graph_artifact.project_id:
            raise ValueError("editorial project and graph artifact project IDs differ")
        stored = StoredEditorialProject(
            editorial_project=project,
            graph_artifact=graph_artifact,
            updated_at=self._now(),
        )
        self._client.collection(self._editorial_projects_collection).document(
            str(project.editorial_project_id)
        ).set(
            {
                "schema_version": 1,
                **stored.model_dump(mode="json"),
            }
        )
        return stored

    def get_editorial_project(
        self,
        editorial_project_id: UUID,
    ) -> StoredEditorialProject | None:
        snapshot = self._client.collection(
            self._editorial_projects_collection
        ).document(str(editorial_project_id)).get()
        if not snapshot.exists:
            return None
        data = snapshot.to_dict()
        if data.get("schema_version") != 1:
            raise ValueError("unsupported editorial project schema version")
        payload = {key: value for key, value in data.items() if key != "schema_version"}
        return StoredEditorialProject.model_validate(payload)

    def save_article(self, publication: PublishedArticle) -> None:
        self._client.collection(self._articles_collection).document(
            str(publication.article_id)
        ).set(
            {
                "schema_version": 1,
                **publication.model_dump(mode="json"),
            }
        )


class GcpEditorialPersistence:
    """Coordinates Storage and Firestore without leaking either into workflows."""

    def __init__(
        self,
        *,
        artifacts: GcsArtifactStore,
        repository: FirestoreEditorialRepository,
    ) -> None:
        self.artifacts = artifacts
        self.repository = repository

    @classmethod
    def from_settings(cls, settings: GcpPersistenceSettings) -> GcpEditorialPersistence:
        storage_client = storage.Client(project=settings.project)
        firestore_client = firestore.Client(project=settings.project)
        return cls(
            artifacts=GcsArtifactStore(
                storage_client,
                bucket_name=settings.bucket,
                root_prefix=settings.root_prefix,
            ),
            repository=FirestoreEditorialRepository(
                firestore_client,
                editorial_projects_collection=settings.editorial_projects_collection,
                articles_collection=settings.articles_collection,
            ),
        )

    def persist_planning_result(
        self,
        project: EditorialProject,
        graph_directory: Path,
    ) -> StoredEditorialProject:
        graph_artifact = self.artifacts.upload_graph(
            project.project_id,
            graph_directory,
        )
        return self.repository.save_editorial_project(project, graph_artifact)

    def save_editorial_project(
        self,
        project: EditorialProject,
    ) -> StoredEditorialProject:
        existing = self.repository.get_editorial_project(project.editorial_project_id)
        if existing is None:
            raise KeyError(
                f"editorial project not found: {project.editorial_project_id}"
            )
        return self.repository.save_editorial_project(project, existing.graph_artifact)

    def get_editorial_project(
        self,
        editorial_project_id: UUID,
    ) -> StoredEditorialProject | None:
        return self.repository.get_editorial_project(editorial_project_id)

    def restore_graph(
        self,
        stored: StoredEditorialProject,
        destination: Path,
    ) -> None:
        self.artifacts.restore_graph(stored.graph_artifact, destination)

    def publish_article(self, article: FinalArticle) -> PublishedArticle:
        publication = self.artifacts.upload_article(article)
        self.repository.save_article(publication)
        return publication


def _sha256_file(path: Path) -> str:
    digest = sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _validate_artifact_path(path: str) -> None:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or not candidate.parts or ".." in candidate.parts:
        raise ValueError(f"unsafe graph artifact path: {path}")


def _validate_manifest(manifest: dict[str, Any], artifact: GraphArtifact) -> None:
    if manifest.get("schema_version") != 1:
        raise ValueError("unsupported graph manifest schema version")
    if manifest.get("project_id") != str(artifact.project_id):
        raise ValueError("graph manifest project ID mismatch")
    if manifest.get("artifact_id") != str(artifact.artifact_id):
        raise ValueError("graph manifest artifact ID mismatch")
    manifest_files = [
        GraphArtifactFile.model_validate(item) for item in manifest["files"]
    ]
    if manifest_files != artifact.files:
        raise ValueError("graph manifest file list mismatch")

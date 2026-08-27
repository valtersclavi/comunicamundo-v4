"""Fresh GCP persistence for the clean rebuild approval boundaries."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
from pathlib import Path, PurePosixPath
from typing import Any, Callable
from uuid import UUID, uuid4

from google.cloud import firestore, storage
from pydantic import Field

from .contracts import (
    ChunkManifest,
    ChunkManifestEntry,
    EditorialBrief,
    EditorialProject,
    FinalArticle,
    GraphExtraction,
    GraphExtractionManifest,
    NarrativeAxis,
    PlanningRequest,
    PlanningRun,
    PreparedChunk,
    SourceLineage,
    StrictModel,
)


def _now() -> datetime:
    return datetime.now(UTC)


class GraphArtifactFile(StrictModel):
    path: str = Field(min_length=1)
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class GraphArtifact(StrictModel):
    project_id: UUID
    artifact_id: UUID
    bucket: str
    prefix: str
    manifest_uri: str = Field(pattern=r"^gs://")
    files: list[GraphArtifactFile] = Field(min_length=1)
    created_at: datetime


class StoredNarrativeAxis(StrictModel):
    narrative_axis: NarrativeAxis
    brief: EditorialBrief
    graph_artifact: GraphArtifact
    graph_context: str = Field(min_length=1)
    editorial_project_id: UUID | None = None
    updated_at: datetime


class StoredEditorialProject(StrictModel):
    editorial_project: EditorialProject
    graph_artifact: GraphArtifact
    graph_context: str = Field(min_length=1)
    updated_at: datetime


class PublishedArticle(StrictModel):
    article_id: UUID
    project_id: UUID
    editorial_project_id: UUID
    title: str
    body_uri: str = Field(pattern=r"^gs://")
    word_count: int = Field(ge=1)
    quality_score: int = Field(ge=0, le=100)
    revision_rounds: int = Field(ge=0, le=3)
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GcpPersistenceSettings:
    project: str
    bucket: str
    root_prefix: str = "comunicamundo-v4-rebuild"
    axes_collection: str = "hefesto_v4_rebuild_axes"
    projects_collection: str = "hefesto_v4_rebuild_projects"
    articles_collection: str = "hefesto_v4_rebuild_articles"
    planning_runs_collection: str = "hefesto_v4_rebuild_planning_runs"
    planning_intents_collection: str = "hefesto_v4_rebuild_planning_intents"
    source_lineages_collection: str = "hefesto_v4_rebuild_source_lineages"
    source_lineage_keys_collection: str = "hefesto_v4_rebuild_source_lineage_keys"


def source_lineage_lookup_id(project_id: UUID, source_fingerprint: str) -> str:
    return sha256(f"{project_id}:{source_fingerprint}".encode("utf-8")).hexdigest()


def _validate_relative(path: str) -> None:
    pure = PurePosixPath(path)
    if pure.is_absolute() or ".." in pure.parts:
        raise ValueError(f"invalid graph artifact path: {path}")


class GcsArtifactStore:
    def __init__(self, client: Any, *, bucket_name: str, root_prefix: str, id_factory: Callable[[], UUID] = uuid4, now: Callable[[], datetime] = _now) -> None:
        self._bucket = client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._root = root_prefix.strip("/")
        self._id_factory = id_factory
        self._now = now
        if not self._root:
            raise ValueError("root_prefix cannot be empty")

    def upload_graph(self, project_id: UUID, directory: Path) -> GraphArtifact:
        files_on_disk = sorted(p for p in directory.rglob("*") if p.is_file())
        if not files_on_disk:
            raise ValueError("graph persistence produced no files")
        artifact_id = self._id_factory()
        prefix = f"{self._root}/projects/{project_id}/graphs/{artifact_id}"
        records: list[GraphArtifactFile] = []
        for local in files_on_disk:
            relative = local.relative_to(directory).as_posix()
            _validate_relative(relative)
            data = local.read_bytes()
            record = GraphArtifactFile(path=relative, size_bytes=len(data), sha256=sha256(data).hexdigest())
            self._bucket.blob(f"{prefix}/files/{relative}").upload_from_string(data)
            records.append(record)
        created_at = self._now()
        manifest_name = f"{prefix}/manifest.json"
        manifest = {
            "schema_version": 1,
            "project_id": str(project_id),
            "artifact_id": str(artifact_id),
            "created_at": created_at.isoformat(),
            "files": [r.model_dump(mode="json") for r in records],
        }
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
            files=records,
            created_at=created_at,
        )

    def restore_graph(self, artifact: GraphArtifact, destination: Path) -> None:
        if artifact.bucket != self._bucket_name:
            raise ValueError("graph artifact belongs to another bucket")
        manifest_name = f"{artifact.prefix}/manifest.json"
        manifest = json.loads(self._bucket.blob(manifest_name).download_as_bytes().decode("utf-8"))
        if manifest.get("schema_version") != 1 or manifest.get("artifact_id") != str(artifact.artifact_id):
            raise ValueError("graph manifest mismatch")
        destination.mkdir(parents=True, exist_ok=True)
        if any(destination.iterdir()):
            raise ValueError("graph restore destination must be empty")
        for record in artifact.files:
            _validate_relative(record.path)
            data = self._bucket.blob(f"{artifact.prefix}/files/{record.path}").download_as_bytes()
            if len(data) != record.size_bytes or sha256(data).hexdigest() != record.sha256:
                raise ValueError(f"graph artifact integrity failure: {record.path}")
            target = destination / record.path
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)

    def persist_planning_json(self, *, project_id: UUID, planning_run_id: UUID, relative_path: str, payload: dict[str, Any]) -> str:
        _validate_relative(relative_path)
        name = f"{self._root}/projects/{project_id}/planning/{planning_run_id}/{relative_path}"
        return self._upload_json(name, payload)

    def persist_source_lineage_json(self, *, project_id: UUID, source_lineage_id: UUID, relative_path: str, payload: dict[str, Any]) -> str:
        _validate_relative(relative_path)
        name = f"{self._root}/projects/{project_id}/sources/{source_lineage_id}/{relative_path}"
        return self._upload_json(name, payload)

    def _upload_json(self, name: str, payload: dict[str, Any]) -> str:
        data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        self._bucket.blob(name).upload_from_string(data, content_type="application/json")
        return f"gs://{self._bucket_name}/{name}"

    def load_json_uri(self, uri: str) -> dict[str, Any]:
        prefix = f"gs://{self._bucket_name}/"
        if not uri.startswith(prefix):
            raise ValueError("artifact belongs to another bucket")
        name = uri[len(prefix):]
        return json.loads(self._bucket.blob(name).download_as_bytes().decode("utf-8"))

    def upload_article(self, article: FinalArticle) -> PublishedArticle:
        article_id = self._id_factory()
        name = f"{self._root}/projects/{article.project_id}/articles/{article_id}.md"
        self._bucket.blob(name).upload_from_string(article.body_markdown.encode("utf-8"), content_type="text/markdown; charset=utf-8")
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
    def __init__(
        self,
        client: Any,
        *,
        axes_collection: str,
        projects_collection: str,
        articles_collection: str,
        planning_runs_collection: str,
        planning_intents_collection: str,
        source_lineages_collection: str,
        source_lineage_keys_collection: str,
        now: Callable[[], datetime] = _now,
    ) -> None:
        self._client = client
        self._axes = axes_collection
        self._projects = projects_collection
        self._articles = articles_collection
        self._planning_runs = planning_runs_collection
        self._planning_intents = planning_intents_collection
        self._source_lineages = source_lineages_collection
        self._source_lineage_keys = source_lineage_keys_collection
        self._now = now

    def save_axis(self, stored: StoredNarrativeAxis) -> StoredNarrativeAxis:
        updated = stored.model_copy(update={"updated_at": self._now()})
        self._client.collection(self._axes).document(str(updated.narrative_axis.narrative_axis_id)).set({"schema_version": 1, **updated.model_dump(mode="json")})
        return updated

    def get_axis(self, axis_id: UUID) -> StoredNarrativeAxis | None:
        snap = self._client.collection(self._axes).document(str(axis_id)).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        if data.pop("schema_version", None) != 1:
            raise ValueError("unsupported Narrative Axis schema")
        return StoredNarrativeAxis.model_validate(data)

    def save_project(self, stored: StoredEditorialProject) -> StoredEditorialProject:
        updated = stored.model_copy(update={"updated_at": self._now()})
        self._client.collection(self._projects).document(str(updated.editorial_project.editorial_project_id)).set({"schema_version": 1, **updated.model_dump(mode="json")})
        return updated

    def get_project(self, project_id: UUID) -> StoredEditorialProject | None:
        snap = self._client.collection(self._projects).document(str(project_id)).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        if data.pop("schema_version", None) != 1:
            raise ValueError("unsupported Editorial Project schema")
        return StoredEditorialProject.model_validate(data)

    def save_article(self, article: PublishedArticle) -> None:
        self._client.collection(self._articles).document(str(article.article_id)).set({"schema_version": 1, **article.model_dump(mode="json")})

    def save_planning_run(self, run: PlanningRun) -> PlanningRun:
        updated = run.model_copy(update={"updated_at": self._now()})
        payload = {"schema_version": 1, **updated.model_dump(mode="json")}
        self._client.collection(self._planning_runs).document(str(updated.planning_run_id)).set(payload)
        self._client.collection(self._planning_intents).document(updated.request_fingerprint).set(
            {
                "schema_version": 1,
                "project_id": str(updated.project_id),
                "request_fingerprint": updated.request_fingerprint,
                "source_fingerprint": updated.source_fingerprint,
                "brief_fingerprint": updated.brief_fingerprint,
                "planning_run_id": str(updated.planning_run_id),
                "source_lineage_id": str(updated.source_lineage_id),
                "updated_at": updated.updated_at.isoformat(),
            }
        )
        return updated

    def get_planning_run(self, planning_run_id: UUID) -> PlanningRun | None:
        snap = self._client.collection(self._planning_runs).document(str(planning_run_id)).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        if data.pop("schema_version", None) != 1:
            raise ValueError("unsupported PlanningRun schema")
        return PlanningRun.model_validate(data)

    def find_planning_run(self, project_id: UUID, request_fingerprint: str) -> PlanningRun | None:
        snap = self._client.collection(self._planning_intents).document(request_fingerprint).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        if data.get("schema_version") != 1:
            raise ValueError("unsupported planning intent schema")
        if data.get("project_id") != str(project_id):
            raise ValueError("planning intent project mismatch")
        run = self.get_planning_run(UUID(data["planning_run_id"]))
        if run is None:
            raise ValueError("planning intent points to a missing run")
        return run

    def save_source_lineage(self, lineage: SourceLineage) -> SourceLineage:
        updated = lineage.model_copy(update={"updated_at": self._now()})
        self._client.collection(self._source_lineages).document(str(updated.source_lineage_id)).set({"schema_version": 1, **updated.model_dump(mode="json")})
        self._client.collection(self._source_lineage_keys).document(source_lineage_lookup_id(updated.project_id, updated.source_fingerprint)).set(
            {
                "schema_version": 1,
                "project_id": str(updated.project_id),
                "source_fingerprint": updated.source_fingerprint,
                "source_lineage_id": str(updated.source_lineage_id),
                "updated_at": updated.updated_at.isoformat(),
            }
        )
        return updated

    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None:
        snap = self._client.collection(self._source_lineages).document(str(source_lineage_id)).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        if data.pop("schema_version", None) != 1:
            raise ValueError("unsupported SourceLineage schema")
        return SourceLineage.model_validate(data)

    def find_source_lineage(self, project_id: UUID, source_fingerprint: str) -> SourceLineage | None:
        snap = self._client.collection(self._source_lineage_keys).document(source_lineage_lookup_id(project_id, source_fingerprint)).get()
        if not snap.exists:
            return None
        data = snap.to_dict()
        if data.get("schema_version") != 1:
            raise ValueError("unsupported source lineage key schema")
        if data.get("project_id") != str(project_id):
            raise ValueError("source lineage project mismatch")
        lineage = self.get_source_lineage(UUID(data["source_lineage_id"]))
        if lineage is None:
            raise ValueError("source lineage key points to a missing lineage")
        return lineage


class GcpEditorialPersistence:
    def __init__(self, *, artifacts: GcsArtifactStore, repository: FirestoreEditorialRepository) -> None:
        self.artifacts = artifacts
        self.repository = repository

    @classmethod
    def from_settings(cls, settings: GcpPersistenceSettings) -> "GcpEditorialPersistence":
        return cls(
            artifacts=GcsArtifactStore(storage.Client(project=settings.project), bucket_name=settings.bucket, root_prefix=settings.root_prefix),
            repository=FirestoreEditorialRepository(
                firestore.Client(project=settings.project),
                axes_collection=settings.axes_collection,
                projects_collection=settings.projects_collection,
                articles_collection=settings.articles_collection,
                planning_runs_collection=settings.planning_runs_collection,
                planning_intents_collection=settings.planning_intents_collection,
                source_lineages_collection=settings.source_lineages_collection,
                source_lineage_keys_collection=settings.source_lineage_keys_collection,
            ),
        )

    def persist_new_axis(self, *, axis: NarrativeAxis, brief: EditorialBrief, graph_context: str, graph_directory: Path) -> StoredNarrativeAxis:
        artifact = self.artifacts.upload_graph(axis.project_id, graph_directory)
        return self.repository.save_axis(StoredNarrativeAxis(
            narrative_axis=axis,
            brief=brief,
            graph_artifact=artifact,
            graph_context=graph_context,
            updated_at=_now(),
        ))

    def save_axis(self, stored: StoredNarrativeAxis) -> StoredNarrativeAxis:
        return self.repository.save_axis(stored)

    def get_axis(self, axis_id: UUID) -> StoredNarrativeAxis | None:
        return self.repository.get_axis(axis_id)

    def save_project(self, stored: StoredEditorialProject) -> StoredEditorialProject:
        return self.repository.save_project(stored)

    def get_project(self, project_id: UUID) -> StoredEditorialProject | None:
        return self.repository.get_project(project_id)

    def restore_graph(self, artifact: GraphArtifact, destination: Path) -> None:
        self.artifacts.restore_graph(artifact, destination)

    def publish_article(self, article: FinalArticle) -> PublishedArticle:
        publication = self.artifacts.upload_article(article)
        self.repository.save_article(publication)
        return publication

    def save_run(self, run: PlanningRun) -> PlanningRun:
        return self.repository.save_planning_run(run)

    def get_run(self, planning_run_id: UUID) -> PlanningRun | None:
        return self.repository.get_planning_run(planning_run_id)

    def find_run(self, project_id: UUID, request_fingerprint: str) -> PlanningRun | None:
        return self.repository.find_planning_run(project_id, request_fingerprint)

    def save_source_lineage(self, lineage: SourceLineage) -> SourceLineage:
        return self.repository.save_source_lineage(lineage)

    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None:
        return self.repository.get_source_lineage(source_lineage_id)

    def find_source_lineage(self, project_id: UUID, source_fingerprint: str) -> SourceLineage | None:
        return self.repository.find_source_lineage(project_id, source_fingerprint)

    def persist_request(self, run: PlanningRun, request: PlanningRequest) -> str:
        return self.artifacts.persist_planning_json(
            project_id=run.project_id,
            planning_run_id=run.planning_run_id,
            relative_path="request.json",
            payload=request.model_dump(mode="json"),
        )

    def persist_chunks(self, lineage: SourceLineage, chunks: list[PreparedChunk]) -> str:
        if lineage.chunk_manifest_uri is not None:
            raise ValueError("source lineage chunks already persisted")
        if not chunks:
            raise ValueError("cannot persist an empty chunk manifesto")
        entries: list[ChunkManifestEntry] = []
        for chunk in chunks:
            chunk_uri = self.artifacts.persist_source_lineage_json(
                project_id=lineage.project_id,
                source_lineage_id=lineage.source_lineage_id,
                relative_path=f"chunks/{chunk.chunk_id}.json",
                payload=chunk.model_dump(mode="json"),
            )
            entries.append(
                ChunkManifestEntry(
                    chunk_id=chunk.chunk_id,
                    source_id=chunk.source_id,
                    source_ordinal=chunk.source_ordinal,
                    chunk_ordinal=chunk.chunk_ordinal,
                    sequence=chunk.sequence,
                    content_sha256=chunk.content_sha256,
                    status=chunk.status,
                    chunk_uri=chunk_uri,
                    extraction_ref=chunk.extraction_ref,
                )
            )
        manifest = ChunkManifest(
            source_lineage_id=lineage.source_lineage_id,
            project_id=lineage.project_id,
            source_fingerprint=lineage.source_fingerprint,
            chunk_count=len(entries),
            chunks=entries,
        )
        return self.artifacts.persist_source_lineage_json(
            project_id=lineage.project_id,
            source_lineage_id=lineage.source_lineage_id,
            relative_path="chunks/manifest.json",
            payload=manifest.model_dump(mode="json"),
        )

    def load_chunk_manifest(self, uri: str) -> ChunkManifest:
        return ChunkManifest.model_validate(self.artifacts.load_json_uri(uri))

    def load_chunk(self, uri: str) -> PreparedChunk:
        return PreparedChunk.model_validate(self.artifacts.load_json_uri(uri))

    def persist_graph_extraction(self, lineage: SourceLineage, extraction: GraphExtraction) -> str:
        return self.artifacts.persist_source_lineage_json(
            project_id=lineage.project_id,
            source_lineage_id=lineage.source_lineage_id,
            relative_path=f"graph-extractions/{extraction.chunk_id}.json",
            payload=extraction.model_dump(mode="json"),
        )

    def persist_graph_extraction_manifest(self, lineage: SourceLineage, manifest: GraphExtractionManifest) -> str:
        return self.artifacts.persist_source_lineage_json(
            project_id=lineage.project_id,
            source_lineage_id=lineage.source_lineage_id,
            relative_path="graph-extractions/manifest.json",
            payload=manifest.model_dump(mode="json"),
        )

    def load_graph_extraction_manifest(self, uri: str) -> GraphExtractionManifest:
        return GraphExtractionManifest.model_validate(self.artifacts.load_json_uri(uri))

    def load_graph_extraction(self, uri: str) -> GraphExtraction:
        return GraphExtraction.model_validate(self.artifacts.load_json_uri(uri))

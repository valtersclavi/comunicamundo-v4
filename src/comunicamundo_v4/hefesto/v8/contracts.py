from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
import json
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator
from ..contracts import EditorialBrief, SourceInput


def identity(value: object) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), default=lambda v: v.model_dump(mode="json") if isinstance(v, BaseModel) else str(v) if isinstance(v, UUID) else (_ for _ in ()).throw(TypeError(type(v).__name__))).encode()).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Input(Model):
    project_id: UUID = Field(default_factory=uuid4)
    brief: EditorialBrief
    sources: list[SourceInput] = Field(default_factory=list)


class Location(Model):
    location_id: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    page: int | None = None
    section: str | None = None


class Source(Model):
    source_id: UUID
    title: str | None = None
    author: str | None = None
    publisher: str | None = None
    origin: str
    doi: str | None = None
    original_uri: str
    binary_sha256: str
    text_sha256: str
    extraction_method: str
    text: str = Field(min_length=1)
    tokens: int = Field(gt=0)
    locations: list[Location] = Field(min_length=1)
    metadata: dict = Field(default_factory=dict)

    @model_validator(mode="after")
    def integrity(self):
        if sha256(self.text.encode()).hexdigest() != self.text_sha256:
            raise ValueError("source text checksum mismatch")
        ids = set()
        previous = 0
        for loc in self.locations:
            if loc.location_id in ids or not previous <= loc.start < loc.end <= len(self.text):
                raise ValueError("invalid, duplicate or overlapping source location")
            ids.add(loc.location_id)
            previous = loc.end
        return self


class Corpus(Model):
    manifest_id: str
    source_ids: list[UUID]
    source_versions: dict[str, str]
    content_tokens: int
    blocks: list[str] = Field(min_length=1, max_length=2)
    block_tokens: list[int]
    useful_tokens: int = Field(gt=0)
    budget: dict


class EditorialProject(Model):
    version: int = Field(ge=1)
    document: str = Field(min_length=1)
    corpus_manifest_id: str
    approved_at: str | None = None


class Remoque(Model):
    message: str = Field(min_length=1)
    base_version: int = Field(ge=1)


class Reference(Model):
    source_id: UUID
    location_id: str


class SupportReference(Model):
    claim: str = Field(min_length=1)
    references: list[Reference] = Field(min_length=1)


class GeneratedWork(Model):
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    support: list[SupportReference] = Field(default_factory=list)


class Reading(Model):
    reading: str = Field(min_length=1)
    references: list[Reference] = Field(min_length=1)


class Proposal(Model):
    document: str = Field(min_length=1)


class SupportAssessment(Model):
    sufficient: bool
    reason: str = Field(min_length=1)
    research_direction: str = ""

    @model_validator(mode="after")
    def direction(self):
        if not self.sufficient and not self.research_direction.strip():
            raise ValueError("insufficient support requires research direction")
        return self


class Issue(Model):
    kind: Literal["briefing", "unsupported", "contradiction", "repetition", "duration", "truncation", "consistency"]
    problem: str = Field(min_length=1)
    target: str = Field(min_length=1)
    references: list[Reference] = Field(default_factory=list)


class Verification(Model):
    issues: list[Issue] = Field(default_factory=list)
    support: list[SupportReference] = Field(default_factory=list)


class Patch(Model):
    replacement: str
    support: list[SupportReference] = Field(default_factory=list)


class SearchDecision(Model):
    decision: Literal["search", "success", "no_reliable_support"]
    reason: str = Field(min_length=1)
    queries: list[str] = Field(default_factory=list)
    selected_ids: list[str] = Field(default_factory=list)


class IdentityAssessment(Model):
    same_document: bool
    complete: bool
    reliable_support: bool
    reason: str = Field(min_length=1)


class State(Model):
    execution_id: UUID
    owner_id: str
    request: Input
    revision: int = 0
    status: str = "queued"
    sources: list[Source] = Field(default_factory=list)
    ingestion: dict = Field(default_factory=dict)
    corpus: Corpus | None = None
    projects: list[EditorialProject] = Field(default_factory=list)
    remoque: list[Remoque] = Field(default_factory=list)
    work: GeneratedWork | None = None
    work_versions: list[GeneratedWork] = Field(default_factory=list)
    verifications: list[Verification] = Field(default_factory=list)
    research: dict = Field(default_factory=dict)
    action: dict = Field(default_factory=lambda: {"kind": "plan"})
    completed_actions: list[str] = Field(default_factory=list)
    telemetry: list[dict] = Field(default_factory=list)
    error: dict | None = None
    job: dict = Field(default_factory=dict)
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat())

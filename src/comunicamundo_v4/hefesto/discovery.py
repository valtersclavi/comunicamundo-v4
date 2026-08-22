"""Optional source-discovery boundary between Hermes and Hefesto."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence
from uuid import UUID

from .contracts import EditorialBrief, EditorialSubject, NormalizedSource


@dataclass(frozen=True, slots=True)
class SourceDiscoveryRequest:
    """A request for sources, not for an answer or a finished synthesis."""

    project_id: UUID
    brief: EditorialBrief
    existing_sources: tuple[NormalizedSource, ...]
    subject: EditorialSubject | None = None
    research_gap: str | None = None


class SourceDiscoveryPort(Protocol):
    """The only contract an optional Hermes adapter must implement."""

    async def discover(
        self,
        request: SourceDiscoveryRequest,
    ) -> Sequence[NormalizedSource]: ...

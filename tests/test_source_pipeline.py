from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from typing import Any

import pytest
import requests

from comunicamundo_v4.hefesto.contracts import SourceCandidate, SourceSearchPlan
from comunicamundo_v4.hefesto.contracts import (
    CallUsage,
    AcquiredSource,
    EditorialBrief,
    NarrativeAxisProposal,
    ProvisionalEditorialProject,
    ProvisionalEditorialSubject,
    SourceSelectionProposal,
)
from comunicamundo_v4.hefesto.gateway import ModelResult
from comunicamundo_v4.hefesto.source_research import SourceResearch
from comunicamundo_v4.hefesto.source_pipeline import (
    AcquisitionPayload,
    OpenAlexSearchProvider,
    SourceAcquirer,
    SourceDiscovery,
    YouSearchProvider,
    candidate_identity,
    canonical_doi,
    canonical_url,
    deduplicate_candidates,
)


@dataclass
class Response:
    payload: Any = None
    text: str = ""
    content: bytes = b""
    url: str = "https://example.org/final"
    content_type: str = "text/html"
    status: int = 200

    @property
    def headers(self) -> dict[str, str]:
        return {"content-type": self.content_type}

    def json(self) -> Any:
        return self.payload

    def raise_for_status(self) -> None:
        if self.status >= 400:
            raise requests.HTTPError(str(self.status))


class Session:
    def __init__(self) -> None:
        self.gets: list[tuple[str, dict[str, Any]]] = []
        self.posts: list[tuple[str, dict[str, Any]]] = []
        self.responses: dict[tuple[str, str], list[Response]] = {}

    def queue(self, method: str, url: str, *responses: Response) -> None:
        self.responses[(method, url)] = list(responses)

    def _take(self, method: str, url: str, values: dict[str, Any]) -> Response:
        getattr(self, f"{method.lower()}s").append((url, values))
        queued = self.responses.get((method, url), [])
        if not queued:
            raise AssertionError(f"unexpected {method} {url}")
        return queued.pop(0)

    def get(self, url: str, **values: Any) -> Response:
        return self._take("GET", url, values)

    def post(self, url: str, **values: Any) -> Response:
        return self._take("POST", url, values)


def candidate(**updates: Any) -> SourceCandidate:
    values = {
        "candidate_id": candidate_identity(doi=None, url="https://example.org/article"),
        "provider": "you",
        "query": "editorial query",
        "rank": 1,
        "title": "A Complete Editorial Source",
        "url": "https://example.org/article",
        "acquisition_url": "https://example.org/article",
    }
    values.update(updates)
    return SourceCandidate.model_validate(values)


def long_text(title: str = "A Complete Editorial Source") -> str:
    return f"# {title}\n\n" + "substantive editorial evidence " * 700


def test_canonical_identity_removes_tracking_and_normalizes_doi() -> None:
    assert canonical_url("https://EXAMPLE.org//paper/?utm_source=x&b=2&a=1#part") == (
        "https://example.org/paper/?a=1&b=2"
    )
    assert canonical_doi("https://doi.org/10.1000/ABC") == "10.1000/abc"
    assert candidate_identity(doi="10.1000/abc", url="https://example.org/a") == (
        candidate_identity(doi="10.1000/abc", url="https://example.org/b")
    )


def test_search_providers_normalize_and_discovery_deduplicates() -> None:
    session = Session()
    session.queue("POST", "https://ydc-index.io/v1/search", Response(payload={
        "results": {"web": [{
            "title": "Shared Paper", "url": "https://publisher.org/paper?utm_source=you",
            "description": "result", "page_age": "2025-01-02",
        }]},
    }))
    session.queue("GET", "https://api.openalex.org/works", Response(payload={
        "results": [{
            "display_name": "Academic Paper",
            "doi": "https://doi.org/10.1000/XYZ",
            "publication_date": "2024-04-03",
            "primary_location": {
                "landing_page_url": "https://publisher.org/work",
                "source": {"display_name": "Journal"},
            },
            "best_oa_location": {
                "landing_page_url": "https://repository.org/work",
                "pdf_url": "https://repository.org/work.pdf",
            },
        }]},
    ))
    discovery = SourceDiscovery([
        YouSearchProvider("you-key", session=session),
        OpenAlexSearchProvider("openalex-key", session=session),
    ], results_per_query=5)

    found = discovery.discover(SourceSearchPlan(
        queries=["query"], editorial_coverage=["coverage"],
    ))

    assert [item.provider for item in found] == ["you", "openalex"]
    assert found[0].url == "https://publisher.org/paper"
    assert found[1].doi == "10.1000/xyz"
    assert found[1].acquisition_url == "https://repository.org/work.pdf"

    duplicate = found[0].model_copy(update={
        "provider": "openalex", "acquisition_url": "https://publisher.org/paper.pdf",
    })
    assert deduplicate_candidates([found[0], duplicate])[0].acquisition_url.endswith(".pdf")


def test_discovery_discards_unsafe_candidate_urls_and_keeps_valid_results() -> None:
    session = Session()
    session.queue("POST", "https://ydc-index.io/v1/search", Response(payload={
        "results": {"web": [
            {"title": "Bad", "url": "https://user:pass@example.org/private"},
            {"title": "Good", "url": "https://example.org/good?utm_source=x"},
        ]},
    }))
    session.queue("GET", "https://api.openalex.org/works", Response(payload={
        "results": [{
            "display_name": "Bad OA",
            "doi": None,
            "primary_location": {"landing_page_url": "http://example.org/openalex"},
        }]},
    ))

    found = SourceDiscovery([
        YouSearchProvider("you-key", session=session),
        OpenAlexSearchProvider("openalex-key", session=session),
    ]).discover(SourceSearchPlan(queries=["query"], editorial_coverage=["axis"]))

    assert [item.title for item in found] == ["Good"]
    assert found[0].url == "https://example.org/good"


def test_discovery_ignores_provider_failure_when_another_provider_returns_sources() -> None:
    good = candidate()

    class BrokenProvider:
        name = "broken"

        def search(self, query, *, count):
            raise ValueError("source URLs must be public HTTPS URLs without credentials")

    class GoodProvider:
        name = "good"

        def search(self, query, *, count):
            return [good]

    found = SourceDiscovery([BrokenProvider(), GoodProvider()]).discover(
        SourceSearchPlan(queries=["query"], editorial_coverage=["axis"])
    )

    assert found == [good]


def test_acquire_falls_through_to_you_contents_and_preserves_attempts() -> None:
    session = Session()
    session.queue("GET", "https://example.org/article", Response(
        text="login", content=b"login", content_type="text/html",
    ))
    session.queue("POST", "https://ydc-index.io/v1/contents", Response(payload=[{
        "url": "https://example.org/article", "title": "A Complete Editorial Source",
        "markdown": long_text(),
    }]))
    acquirer = SourceAcquirer(
        you_api_key="you", jina_api_key="jina", browser_use_api_key="browser",
        session=session,
    )

    result = acquirer.acquire(candidate())

    assert result.record.status == "acquired"
    assert result.record.selected_route == "you_contents"
    assert [item.route for item in result.record.attempts] == ["direct", "you_contents"]
    assert result.record.normalized_text_sha256
    assert result.original_bytes == b"login"


def test_acquire_uses_jina_when_contents_is_too_short() -> None:
    session = Session()
    session.queue("GET", "https://example.org/article", Response(status=403))
    session.queue("POST", "https://ydc-index.io/v1/contents", Response(payload=[{
        "markdown": "too short", "title": "A Complete Editorial Source",
    }]))
    session.queue("GET", "https://r.jina.ai/https://example.org/article", Response(
        text=long_text(), content_type="text/markdown",
    ))
    result = SourceAcquirer(
        you_api_key="you", jina_api_key="jina", browser_use_api_key="browser",
        session=session,
    ).acquire(candidate())

    assert result.record.selected_route == "jina_reader"
    assert [item.route for item in result.record.attempts] == [
        "direct", "you_contents", "jina_reader",
    ]


def test_acquire_reports_human_action_without_claiming_success() -> None:
    session = Session()
    session.queue("GET", "https://example.org/article", Response(status=403))
    session.queue("POST", "https://ydc-index.io/v1/contents", Response(payload=[]))
    session.queue("GET", "https://r.jina.ai/https://example.org/article", Response(status=403))
    session.queue("POST", "https://api.browser-use.com/api/v4/runs", Response(payload={"id": "run-1"}))
    session.queue("GET", "https://api.browser-use.com/api/v4/runs/run-1/status", Response(payload={"status": "completed"}))
    session.queue("GET", "https://api.browser-use.com/api/v4/runs/run-1", Response(payload={
        "status": "completed", "result": "HUMAN_ACTION_REQUIRED: login",
    }))
    result = SourceAcquirer(
        you_api_key="you", jina_api_key="jina", browser_use_api_key="browser",
        session=session, browser_poll_seconds=0,
    ).acquire(candidate())

    assert result.record.status == "human_action_required"
    assert result.record.selected_route is None
    assert result.record.failure_reason == "public acquisition requires human action"


@pytest.mark.parametrize("value", [
    "http://example.org", "https://user:pass@example.org", "not-a-url",
])
def test_canonical_url_rejects_unsafe_shapes(value: str) -> None:
    with pytest.raises(ValueError):
        canonical_url(value)


def usage(task: str) -> CallUsage:
    return CallUsage(
        task_type=task, prompt_tokens=1, output_tokens=1, reasoning_tokens=0,
        total_tokens=2, latency_ms=1, status="succeeded",
    )


@pytest.mark.asyncio
async def test_source_research_replaces_failed_selection_until_editor_has_coverage() -> None:
    first, second = candidate(), candidate(
        candidate_id=candidate_identity(doi=None, url="https://example.org/second"),
        title="Second Complete Source", url="https://example.org/second",
        acquisition_url="https://example.org/second", rank=2,
    )

    class Editor:
        selections = 0

        async def search_plan(self, brief, project):
            return ModelResult(
                value=SourceSearchPlan(queries=["initial"], editorial_coverage=["axis"]),
                usage=usage("source_search_plan"),
            )

        async def select(self, brief, project, candidates, **kwargs):
            self.selections += 1
            value = (
                SourceSelectionProposal(
                    selected_candidate_ids=[first.candidate_id],
                    coverage_judgment="first candidate should cover it",
                    coverage_complete=True,
                )
                if self.selections == 1 else
                SourceSelectionProposal(
                    selected_candidate_ids=[second.candidate_id],
                    coverage_judgment="replacement provides sufficient coverage",
                    coverage_complete=True,
                )
            )
            return ModelResult(value=value, usage=usage("source_editor_selection"))

    class Discovery:
        def discover(self, plan):
            return [first, second]

    class Acquirer:
        def acquire(self, item):
            if item.candidate_id == first.candidate_id:
                return AcquisitionPayload(record=AcquiredSource(
                    candidate=item, status="failed", failure_reason="blocked",
                ))
            text = long_text("Second Complete Source")
            return AcquisitionPayload(record=AcquiredSource(
                candidate=item, status="acquired", selected_route="you_contents",
                final_url=item.url, normalized_text=text,
                normalized_text_sha256=sha256(text.encode()).hexdigest(),
            ))

    brief = EditorialBrief(
        theme="Theme", approach="Approach", duration_minutes=10,
        output_language="pt-BR", audience="Audience", product="article",
    )
    project = ProvisionalEditorialProject(
        working_title="Project", editorial_promise="Promise",
        narrative_axis=NarrativeAxisProposal(statement="Axis", rationale="Why"),
        duration_minutes=10,
        subjects=[ProvisionalEditorialSubject(
            title="Subject", editorial_role="Role", desired_depth="Deep",
            allocated_minutes=10,
        )],
        disclaimer="To be sustained by sources found",
    )

    result = await SourceResearch(
        Editor(), Discovery(), Acquirer(), max_search_rounds=2,
    ).run(brief, project)

    assert [item.record.status for item in result.acquisitions] == ["acquired"]
    assert result.failures == ()
    assert result.selection.selected_candidate_ids == [second.candidate_id]
    assert len(result.usages) == 3

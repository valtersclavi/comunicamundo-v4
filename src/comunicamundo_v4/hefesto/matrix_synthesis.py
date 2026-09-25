"""Matrix-backed editorial decisions. Every model call has a bounded input.

The matrix remains the source of truth; these short outputs are presentation and
routing artifacts, never a replacement for stored claims and their evidence.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from hashlib import sha256
import json
from math import ceil
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    EditorialBrief, EditorialCorpus, EditorialEvidence, EditorialKnowledgeUnit,
    EditorialProjectProposal, EditorialProjectScaffold, EditorialSubject,
    PlanningRequest,
)


class Theme(BaseModel):
    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=120)
    insight: str = Field(min_length=1, max_length=320)


class Themes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Theme] = Field(default_factory=list, max_length=4)


class ClaimPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    subject_indexes: list[int] = Field(default_factory=list, max_length=12)
    role: str = Field(pattern="^(core|context|tension|qualification)$")


class Placements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ClaimPlacement] = Field(default_factory=list, max_length=10)


class SubjectReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    synthesis: str = Field(min_length=1, max_length=1600)
    points: list[str] = Field(default_factory=list, max_length=6)
    tensions: list[str] = Field(default_factory=list, max_length=4)
    qualifications: list[str] = Field(default_factory=list, max_length=4)


def _digest(text: str) -> str:
    return sha256(text.encode()).hexdigest()


def _claim_input(claim: dict[str, Any]) -> dict[str, object]:
    return {
        "id": claim["claim_id"], "kind": claim["kind"],
        "statement": claim["statement"],
        "sources": sorted({item["source_ordinal"] for item in claim["evidence"]}),
    }


async def _cached(
    repository: object, execution_id: UUID, round_number: int,
    task: str, prompt: str, schema: type[BaseModel],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
    output_tokens: int,
) -> BaseModel:
    digest = _digest(prompt)
    unit_id = _digest(task + ":" + digest)
    cached = await repository.matrix_work(
        execution_id, round_number, task, unit_id, digest,
    )
    if cached is not None:
        return schema.model_validate(cached)
    result = await call(task, prompt, schema, output_tokens)
    await repository.save_matrix_work(
        execution_id, round_number, task, unit_id, digest,
        result.model_dump(mode="json"),
    )
    return result


async def _themes(
    repository: object, execution_id: UUID, round_number: int,
    brief: EditorialBrief, claims: Sequence[dict[str, Any]],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
) -> list[Theme]:
    themes: list[Theme] = []
    for offset in range(0, len(claims), 8):
        packet = [_claim_input(item) for item in claims[offset:offset + 8]]
        prompt = (
            "Identify up to four distinct editorial ideas across these claims. "
            "Preserve material contradictions, limits and unexpected angles. "
            "Do not invent claims. Output short titles and insights.\n"
            f"BRIEF: {brief.model_dump_json()}\nCLAIMS: "
            f"{json.dumps(packet, ensure_ascii=False)}"
        )
        result = await _cached(
            repository, execution_id, round_number, "matrix_themes",
            prompt, Themes, call, 2_048,
        )
        themes.extend(result.items)
    level = 0
    while len(themes) > 12:
        level += 1
        merged: list[Theme] = []
        for offset in range(0, len(themes), 10):
            prompt = (
                "Group overlapping editorial themes into up to four meaningful "
                "directions. Preserve tensions and distinctive angles. Keep "
                "specific meaning; do not add unsupported facts.\n"
                f"BRIEF: {brief.model_dump_json()}\nTHEMES: "
                + json.dumps(
                    [item.model_dump() for item in themes[offset:offset + 10]],
                    ensure_ascii=False,
                )
            )
            result = await _cached(
                repository, execution_id, round_number,
                f"matrix_theme_merge_{level}", prompt, Themes, call, 2_048,
            )
            merged.extend(result.items)
        if len(merged) >= len(themes):
            raise ValueError("matrix thematic merge did not reduce its input")
        themes = merged
    return themes


async def _route(
    repository: object, execution_id: UUID, round_number: int,
    brief: EditorialBrief, claims: Sequence[dict[str, Any]],
    subjects: Sequence[EditorialSubject],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
) -> dict[int, list[dict[str, Any]]]:
    routed: dict[int, list[dict[str, Any]]] = {
        index: [] for index in range(len(subjects))
    }
    links: dict[int, list[tuple[str, str]]] = {
        index: [] for index in range(len(subjects))
    }
    for offset in range(0, len(claims), 10):
        packet = claims[offset:offset + 10]
        prompt = (
            "Assign each relevant claim to every editorial subject it can "
            "materially develop. Return only IDs present in this packet. "
            "Keep disagreements as tensions; do not turn a model-origin claim "
            "into source evidence. A claim may belong to several subjects or "
            "none.\n"
            f"BRIEF: {brief.model_dump_json()}\nSUBJECTS: "
            + json.dumps(
                [{"index": i, "title": item.title,
                  "role": item.editorial_role[:160]}
                 for i, item in enumerate(subjects)],
                ensure_ascii=False,
            )
            + "\nCLAIMS: "
            + json.dumps([_claim_input(item) for item in packet], ensure_ascii=False)
        )
        result = await _cached(
            repository, execution_id, round_number,
            "matrix_claim_routing", prompt, Placements, call, 2_048,
        )
        known = {item["claim_id"]: item for item in packet}
        seen: set[str] = set()
        for placement in result.items:
            if placement.claim_id not in known or placement.claim_id in seen:
                raise ValueError("matrix route contains an unknown or duplicate claim")
            seen.add(placement.claim_id)
            for index in set(placement.subject_indexes):
                if index < 0 or index >= len(subjects):
                    raise ValueError("matrix route contains an unknown subject")
                routed[index].append(known[placement.claim_id])
                links[index].append((placement.claim_id, placement.role))
        if seen != set(known):
            raise ValueError("matrix routing omitted claims from the packet")
    for index, assignments in links.items():
        await repository.replace_matrix_subject_links(
            execution_id, round_number, index, assignments,
        )
    return routed


async def _reading(
    repository: object, execution_id: UUID, round_number: int,
    subject: EditorialSubject, claims: Sequence[dict[str, Any]],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
) -> SubjectReading:
    readings: list[SubjectReading] = []
    for offset in range(0, len(claims), 8):
        prompt = (
            "Write a concise editorial reading for this subject from these "
            "claims. Preserve qualifications, disagreements and uncertainty. "
            "Do not claim a model-origin contribution came from a source.\n"
            f"SUBJECT: {subject.model_dump_json()}\nCLAIMS: "
            + json.dumps(
                [_claim_input(item) for item in claims[offset:offset + 8]],
                ensure_ascii=False,
            )
        )
        result = await _cached(
            repository, execution_id, round_number, "matrix_subject_reading",
            prompt, SubjectReading, call, 2_048,
        )
        readings.append(result)
    level = 0
    while len(readings) > 1:
        level += 1
        merged: list[SubjectReading] = []
        for offset in range(0, len(readings), 5):
            group = readings[offset:offset + 5]
            if len(group) == 1:
                merged.append(group[0])
                continue
            prompt = (
                "Integrate these partial editorial readings for the same "
                "subject. Preserve distinct ideas, tensions and limits. "
                "This is a short presentation layer; the original claims "
                "remain available to the writer.\n"
                f"SUBJECT: {subject.model_dump_json()}\nREADINGS: "
                + json.dumps(
                    [item.model_dump(mode="json") for item in group],
                    ensure_ascii=False,
                )
            )
            result = await _cached(
                repository, execution_id, round_number,
                f"matrix_subject_merge_{level}", prompt, SubjectReading,
                call, 2_048,
            )
            merged.append(result)
        readings = merged
    return readings[0]


async def propose_from_matrix(
    run: object,
    repository: object,
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
    *,
    round_number: int = 1,
    revision_instruction: str | None = None,
) -> EditorialProjectProposal:
    claims = await repository.matrix_claims(run.execution_run_id)
    if not claims:
        raise ValueError("the editorial matrix has no source contributions")
    themes = await _themes(
        repository, run.execution_run_id, round_number,
        run.request.brief, claims, call,
    )
    provisional = run.request.provisional_project
    previous = run.request.existing_editorial_project
    anchor = (
        "The approved provisional project guides the editorial decision. "
        "Keep supported subjects and their intended depth; do not invent "
        "support for unsupported subjects.\n" + provisional.model_dump_json()
        if provisional is not None else
        "Reevaluate the complete project, including the Narrative Axis, "
        "after enrichment. Preserve existing subjects and their allocated "
        "minutes at minimum; increase total time for genuine new material.\n"
        + previous.model_dump_json()
        if previous is not None else "This is the first project from supplied sources."
    )
    suggestions = (
        "Return empty model_knowledge_suggestions and "
        "source_enrichment_opportunities; this is not a first supplied-source round."
        if provisional is not None or previous is not None or round_number > 1 else
        "Offer distinct small optional model-knowledge contributions, which "
        "need no new source, and broader source-enrichment opportunities, "
        "which require new documentary support. Keep both lists short."
    )
    prompt = (
        "Develop a coherent Editorial Project scaffold with a Narrative Axis "
        "and substantive subjects, grounded in the matrix themes below. "
        "Preserve meaningful contradictions and limits. Do not pretend "
        "the presentation themes exhaust the matrix. Return a compact "
        "governing understanding and forbidden inferences. "
        + suggestions + "\n" + anchor + "\n"
        + (f"USER REVISION: {revision_instruction}\n" if revision_instruction else "")
        + f"BRIEF: {run.request.brief.model_dump_json()}\nTHEMES: "
        + json.dumps([item.model_dump() for item in themes], ensure_ascii=False)
    )
    scaffold = await _cached(
        repository, run.execution_run_id, round_number, "matrix_scaffold",
        prompt, EditorialProjectScaffold, call, 8_192,
    )
    subjects = list(scaffold.source_supported_subjects)
    routed = await _route(
        repository, run.execution_run_id, round_number, run.request.brief,
        claims, subjects, call,
    )
    supported = [(index, item) for index, item in enumerate(subjects)
                 if any(c["origin"] == "source" for c in routed[index])]
    if not supported:
        raise ValueError("matrix did not support any Editorial Project subject")
    if previous is not None:
        prior = {item.title.casefold(): item for item in previous.source_supported_subjects}
        subjects = [
            item.model_copy(update={
                "allocated_minutes": max(
                    item.allocated_minutes,
                    prior[item.title.casefold()].allocated_minutes,
                )
            }) if item.title.casefold() in prior else item
            for _, item in supported
        ]
    else:
        subjects = [item for _, item in supported]
    readings = []
    for original_index, subject in supported:
        value = await _reading(
            repository, run.execution_run_id, round_number,
            subject, routed[original_index], call,
        )
        sources = sorted({
            evidence["source_ordinal"]
            for claim in routed[original_index] if claim["origin"] == "source"
            for evidence in claim["evidence"]
        })
        readings.append(EditorialKnowledgeUnit(
            title=subject.title, synthesis=value.synthesis,
            essential_points=value.points or [value.synthesis[:300]],
            tensions=value.tensions, qualifications=value.qualifications,
            evidence=[EditorialEvidence(
                source_ordinals=[source],
                contribution=next(
                    claim["statement"] for claim in routed[original_index]
                    if any(e["source_ordinal"] == source
                           for e in claim["evidence"])
                ),
            ) for source in sources[:4]],
        ))
    await repository.remap_matrix_subject_links(
        run.execution_run_id, round_number,
        [index for index, _ in supported],
    )
    duration = max(
        scaffold.duration_minutes,
        ceil(sum(item.allocated_minutes for item in subjects)),
    )
    return EditorialProjectProposal(
        working_title=scaffold.working_title,
        editorial_promise=scaffold.editorial_promise,
        narrative_axis=scaffold.narrative_axis,
        duration_minutes=duration,
        source_supported_subjects=subjects,
        editorial_corpus=EditorialCorpus(
            governing_understanding=scaffold.corpus_governing_understanding,
            units=readings,
            forbidden_inferences=scaffold.forbidden_inferences,
        ),
        model_knowledge_suggestions=(
            [] if provisional is not None or previous is not None or round_number > 1
            else scaffold.model_knowledge_suggestions
        ),
        source_enrichment_opportunities=(
            [] if provisional is not None or previous is not None or round_number > 1
            else scaffold.source_enrichment_opportunities
        ),
    )

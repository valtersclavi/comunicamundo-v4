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
    title: str = Field(min_length=1)
    insight: str = Field(min_length=1)


class Themes(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Theme] = Field(default_factory=list)


class MatrixSuggestions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    model_knowledge_suggestions: list[str] = Field(min_length=1)
    source_enrichment_opportunities: list[str] = Field(min_length=1)


class ClaimPlacement(BaseModel):
    model_config = ConfigDict(extra="forbid")
    claim_id: str
    subject_indexes: list[int] = Field(default_factory=list)
    role: str = Field(pattern="^(core|context|tension|qualification)$")


class Placements(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ClaimPlacement] = Field(default_factory=list)


class ClaimRelation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    left_claim_id: str
    right_claim_id: str
    relation: str = Field(pattern="^(converges|complements|diverges)$")
    rationale: str = Field(min_length=1)


class ClaimRelations(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[ClaimRelation] = Field(default_factory=list)


class SubjectReading(BaseModel):
    model_config = ConfigDict(extra="forbid")
    synthesis: str = Field(min_length=1)
    points: list[str] = Field(default_factory=list)
    tensions: list[str] = Field(default_factory=list)
    qualifications: list[str] = Field(default_factory=list)


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


async def _packet_call(
    repository: object, execution_id: UUID, round_number: int,
    task: str, items: Sequence[Any], render: Callable[[Sequence[Any]], str],
    schema: type[BaseModel],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
    output_tokens: int,
) -> list[BaseModel]:
    prompt = render(items)
    digest = _digest(prompt)
    unit_id = _digest(task + ":" + digest)
    cached = await repository.matrix_work(
        execution_id, round_number, task, unit_id, digest,
    )
    if cached is not None and not cached.get("_split"):
        return [schema.model_validate(cached)]
    if cached is None:
        try:
            value = await call(task, prompt, schema, output_tokens)
        except Exception as error:
            limited = (
                "MAX_TOKENS" in str(error).upper()
                or "ATOMIC INPUT BUDGET" in str(error).upper()
            )
            if not limited or len(items) < 2:
                raise
            await repository.save_matrix_work(
                execution_id, round_number, task, unit_id, digest,
                {"_split": True},
            )
        else:
            await repository.save_matrix_work(
                execution_id, round_number, task, unit_id, digest,
                value.model_dump(mode="json"),
            )
            return [value]
    middle = len(items) // 2
    left = await _packet_call(
        repository, execution_id, round_number, task,
        items[:middle], render, schema, call, output_tokens,
    )
    right = await _packet_call(
        repository, execution_id, round_number, task,
        items[middle:], render, schema, call, output_tokens,
    )
    return [*left, *right]


async def _themes(
    repository: object, execution_id: UUID, round_number: int,
    brief: EditorialBrief, claims: Sequence[dict[str, Any]],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
) -> list[Theme]:
    themes: list[Theme] = []
    for offset in range(0, len(claims), 8):
        packet = [_claim_input(item) for item in claims[offset:offset + 8]]
        results = await _packet_call(
            repository, execution_id, round_number, "matrix_themes",
            packet,
            lambda items: (
                "Identify up to four distinct editorial ideas across these claims. "
                "Preserve material contradictions, limits and unexpected angles. "
                "Do not invent claims. Output short titles and insights.\n"
                f"BRIEF: {brief.model_dump_json()}\nCLAIMS: "
                + json.dumps(items, ensure_ascii=False)
            ),
            Themes, call, 2_048,
        )
        for result in results:
            themes.extend(result.items)
    level = 0
    while len(themes) > 12:
        level += 1
        merged: list[Theme] = []
        for offset in range(0, len(themes), 10):
            group = themes[offset:offset + 10]
            results = await _packet_call(
                repository, execution_id, round_number,
                f"matrix_theme_merge_{level}", group,
                lambda items: (
                    "Group overlapping editorial themes into up to four "
                    "meaningful directions. Preserve tensions, limits and "
                    "distinctive angles. Do not add unsupported facts.\n"
                    f"BRIEF: {brief.model_dump_json()}\nTHEMES: "
                    + json.dumps([item.model_dump() for item in items],
                                 ensure_ascii=False)
                ),
                Themes, call, 2_048,
            )
            for result in results:
                merged.extend(result.items)
        if len(merged) >= len(themes):
            raise ValueError("matrix thematic merge did not reduce its input")
        themes = merged
    if not themes:
        raise ValueError("matrix thematic reading produced no supported direction")
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
        results = await _packet_call(
            repository, execution_id, round_number,
            "matrix_claim_routing", packet,
            lambda items: (
                "Assign every listed claim to each editorial subject it "
                "can materially develop. Return each claim ID exactly once "
                "with its subject indexes (or an empty list). Preserve "
                "contradictions and do not treat model knowledge as source "
                "evidence.\n"
                f"BRIEF: {brief.model_dump_json()}\nSUBJECTS: "
                + json.dumps(
                    [{"index": i, "title": subject.title,
                      "role": subject.editorial_role}
                     for i, subject in enumerate(subjects)],
                    ensure_ascii=False,
                )
                + "\nCLAIMS: "
                + json.dumps([_claim_input(item) for item in items],
                             ensure_ascii=False)
            ),
            Placements, call, 2_048,
        )
        known = {item["claim_id"]: item for item in packet}
        placements = [placement for result in results for placement in result.items]

        def valid(items: list[ClaimPlacement]) -> bool:
            ids = [item.claim_id for item in items]
            return (
                len(ids) == len(known)
                and set(ids) == set(known)
                and all(
                    all(0 <= index < len(subjects) for index in item.subject_indexes)
                    for item in items
                )
            )

        if not valid(placements):
            # Repair only this packet. The original output stays checkpointed and
            # the repair has its own cache key for safe continuation.
            repair_prompt = (
                "Correct the claim routing below. Return exactly one placement for "
                "EACH ID in VALID_IDS, with no other IDs, no duplicates, and only "
                "subject indexes from SUBJECTS. Preserve valid placements and their "
                "roles; decide missing claims from their statements. An empty "
                "subject_indexes list is allowed when no subject fits.\n"
                "VALID_IDS: " + json.dumps(list(known)) + "\nSUBJECTS: "
                + json.dumps([{"index": i, "title": subject.title}
                              for i, subject in enumerate(subjects)], ensure_ascii=False)
                + "\nCLAIMS: " + json.dumps(
                    [_claim_input(item) for item in packet], ensure_ascii=False
                )
                + "\nPREVIOUS_PLACEMENTS: " + json.dumps(
                    [item.model_dump(mode="json") for item in placements],
                    ensure_ascii=False,
                )
            )
            repaired = await _cached(
                repository, execution_id, round_number,
                "matrix_claim_routing_repair", repair_prompt, Placements,
                call, 2_048,
            )
            placements = repaired.items
            if not valid(placements):
                raise ValueError("matrix route repair still contains invalid claim IDs or indexes")
        for placement in placements:
            for index in set(placement.subject_indexes):
                routed[index].append(known[placement.claim_id])
                links[index].append((placement.claim_id, placement.role))
    for index, assignments in links.items():
        await repository.replace_matrix_subject_links(
            execution_id, round_number, index, assignments,
        )
    return routed


async def _relations(
    repository: object, execution_id: UUID, round_number: int,
    routed: dict[int, list[dict[str, Any]]],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
) -> None:
    """Relate small windows within each subject, preserving source disagreements."""
    for subject_index, claims in routed.items():
        # Interleave claims by source so a window can compare different sources.
        groups: dict[int, list[dict[str, Any]]] = {}
        for claim in claims:
            ordinals = [e["source_ordinal"] for e in claim["evidence"]]
            groups.setdefault(min(ordinals) if ordinals else 0, []).append(claim)
        ordered = []
        while any(groups.values()):
            for source in sorted(groups):
                if groups[source]:
                    ordered.append(groups[source].pop(0))
        for offset in range(0, len(ordered), 8):
            packet = ordered[offset:offset + 8]
            if len(packet) < 2:
                continue
            results = await _packet_call(
                repository, execution_id, round_number, "matrix_claim_relations",
                packet,
                lambda items: (
                    "Identify only material convergences, complements and "
                    "divergences between these contributions to the same "
                    "editorial subject. Return at most eight pairs; an empty "
                    "list is valid. Do not invent IDs or erase disagreements. "
                    "Model knowledge is not source evidence.\n"
                    f"SUBJECT INDEX: {subject_index}\nCLAIMS: "
                    + json.dumps([_claim_input(item) for item in items],
                                 ensure_ascii=False)
                ),
                ClaimRelations, call, 2_048,
            )
            known = {item["claim_id"] for item in packet}
            validated = []
            for result in results:
                for relation in result.items:
                    if (relation.left_claim_id not in known
                            or relation.right_claim_id not in known
                            or relation.left_claim_id == relation.right_claim_id):
                        raise ValueError("matrix relation contains invalid claim IDs")
                    validated.append((
                        relation.left_claim_id, relation.right_claim_id,
                        relation.relation, relation.rationale,
                    ))
            await repository.save_matrix_relations(execution_id, validated)


async def _reading(
    repository: object, execution_id: UUID, round_number: int,
    subject: EditorialSubject, claims: Sequence[dict[str, Any]],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
) -> SubjectReading:
    readings: list[SubjectReading] = []
    for offset in range(0, len(claims), 8):
        packet = claims[offset:offset + 8]
        results = await _packet_call(
            repository, execution_id, round_number,
            "matrix_subject_reading", packet,
            lambda items: (
                "Write a concise editorial reading from these claims. "
                "Preserve qualifications, disagreements and uncertainty; "
                "distinguish model knowledge from sources.\n"
                f"SUBJECT: {subject.model_dump_json()}\nCLAIMS: "
                + json.dumps([_claim_input(item) for item in items],
                             ensure_ascii=False)
            ),
            SubjectReading, call, 2_048,
        )
        readings.extend(results)
    level = 0
    while len(readings) > 1:
        level += 1
        merged: list[SubjectReading] = []
        for offset in range(0, len(readings), 5):
            group = readings[offset:offset + 5]
            if len(group) == 1:
                merged.append(group[0])
                continue
            results = await _packet_call(
                repository, execution_id, round_number,
                f"matrix_subject_merge_{level}", group,
                lambda items: (
                    "Integrate these partial editorial readings. Preserve "
                    "distinct ideas, tensions and limits. The original "
                    "matrix remains available to the writer.\n"
                    f"SUBJECT: {subject.model_dump_json()}\nREADINGS: "
                    + json.dumps(
                        [item.model_dump(mode="json") for item in items],
                        ensure_ascii=False,
                    )
                ),
                SubjectReading, call, 2_048,
            )
            merged.extend(results)
        readings = merged
    return readings[0]


def _previous_project_context(previous: EditorialProjectProposal | None) -> str:
    if previous is None:
        return ""
    return json.dumps({
        "working_title": previous.working_title,
        "editorial_promise": previous.editorial_promise,
        "narrative_axis": previous.narrative_axis.model_dump(mode="json"),
        "duration_minutes": previous.duration_minutes,
        "subjects": [
            item.model_dump(mode="json")
            for item in previous.source_supported_subjects
        ],
    }, ensure_ascii=False)


async def propose_from_matrix(
    run: object,
    repository: object,
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
    *,
    round_number: int = 1,
    revision_instruction: str | None = None,
    model_choice: bool = False,
) -> EditorialProjectProposal:
    claims = await repository.matrix_claims(run.execution_run_id)
    if not claims:
        raise ValueError("the editorial matrix has no source contributions")
    themes = await _themes(
        repository, run.execution_run_id, round_number,
        run.request.brief, claims, call,
    )
    provisional = run.request.provisional_project
    previous = (
        run.editorial_project.proposal
        if revision_instruction is not None and run.editorial_project is not None
        else run.request.existing_editorial_project
    )
    if model_choice:
        anchor = (
            "Integrate only the chosen small model-knowledge contribution "
            "inside the existing subjects. Preserve title, promise, axis, "
            "subjects and duration. Do not present model knowledge as "
            "documentary evidence.\n" + _previous_project_context(previous)
        )
    elif revision_instruction is not None:
        anchor = (
            "Revise the existing Editorial Project according to the user's "
            "instruction. Add, remove or change subjects, title, promise and "
            "axis as requested; retain relevant supported content. Do not "
            "keep a subject the user explicitly removed.\n"
            + (_previous_project_context(previous))
        )
    else:
        anchor = (
        "The approved provisional project guides the editorial decision. "
        "Keep supported subjects and their intended depth; do not invent "
        "support for unsupported subjects.\n" + provisional.model_dump_json()
        if provisional is not None else
        "Reevaluate the complete project, including the Narrative Axis, "
        "after enrichment. Preserve existing subjects and their allocated "
        "minutes at minimum; increase total time for genuine new material.\n"
        + _previous_project_context(previous)
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
    if model_choice and previous is not None:
        subjects = list(previous.source_supported_subjects)
    elif provisional is not None:
        supported_indexes = run.request.supported_provisional_subject_indexes
        subjects = [
            EditorialSubject(
                title=provisional.subjects[index].title,
                editorial_role=provisional.subjects[index].editorial_role,
                depth=provisional.subjects[index].desired_depth,
                allocated_minutes=provisional.subjects[index].allocated_minutes,
            )
            for index in supported_indexes
        ]
    elif previous is not None and revision_instruction is None:
        existing_titles = {item.title.casefold() for item in subjects}
        subjects.extend(
            item for item in previous.source_supported_subjects
            if item.title.casefold() not in existing_titles
        )
    if not subjects or len(subjects) > 12:
        raise ValueError("matrix project has an invalid number of supported subjects")
    routed = await _route(
        repository, run.execution_run_id, round_number, run.request.brief,
        claims, subjects, call,
    )
    await _relations(repository, run.execution_run_id, round_number, routed, call)
    supported = [(index, item) for index, item in enumerate(subjects)
                 if any(c["origin"] == "source" for c in routed[index])]
    if model_choice and not any(
        claim["origin"] == "model"
        for group in routed.values() for claim in group
    ):
        raise ValueError("chosen model contribution was not integrated into a subject")
    if not supported:
        raise ValueError("matrix did not support any Editorial Project subject")
    if previous is not None and (revision_instruction is None or model_choice):
        retained = {item.title.casefold() for _, item in supported}
        missing = [
            item.title for item in previous.source_supported_subjects
            if item.title.casefold() not in retained
        ]
        if missing:
            raise ValueError(
                "enrichment cannot remove existing subjects: " + ", ".join(missing)
            )
    if previous is not None and (revision_instruction is None or model_choice):
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
            essential_points=value.points or [value.synthesis],
            tensions=value.tensions, qualifications=value.qualifications,
            evidence=[EditorialEvidence(
                source_ordinals=[source],
                contribution=next(
                    claim["statement"] for claim in routed[original_index]
                    if any(e["source_ordinal"] == source
                           for e in claim["evidence"])
                ),
            ) for source in sources],
        ))
    await repository.remap_matrix_subject_links(
        run.execution_run_id, round_number,
        [index for index, _ in supported],
    )
    suggestions_result = None
    if provisional is None and previous is None and round_number == 1:
        suggestion_prompt = (
            "Offer two distinct kinds of optional editorial additions for "
            "this first project. Model-knowledge suggestions must be small "
            "atomic contributions to existing subjects that need no new "
            "source and are explicitly not documentary evidence. Source "
            "enrichment opportunities must be broader gaps or directions "
            "that require finding a new source. Provide one to three of "
            "each type. Do not name or fabricate sources.\n"
            f"BRIEF: {run.request.brief.model_dump_json()}\n"
            f"SUBJECTS: {json.dumps([item.title for item in subjects], ensure_ascii=False)}\n"
            f"AXIS: {scaffold.narrative_axis.model_dump_json()}"
        )
        suggestions_result = await _cached(
            repository, run.execution_run_id, round_number,
            "matrix_suggestions", suggestion_prompt,
            MatrixSuggestions, call, 1_536,
        )
    duration = max(
        scaffold.duration_minutes,
        ceil(sum(item.allocated_minutes for item in subjects)),
    )
    return EditorialProjectProposal(
        working_title=previous.working_title if model_choice and previous else scaffold.working_title,
        editorial_promise=previous.editorial_promise if model_choice and previous else scaffold.editorial_promise,
        narrative_axis=previous.narrative_axis if model_choice and previous else scaffold.narrative_axis,
        duration_minutes=previous.duration_minutes if model_choice and previous else duration,
        source_supported_subjects=subjects,
        editorial_corpus=EditorialCorpus(
            governing_understanding=scaffold.corpus_governing_understanding,
            units=readings,
            forbidden_inferences=scaffold.forbidden_inferences,
        ),
        model_knowledge_suggestions=(
            suggestions_result.model_knowledge_suggestions
            if suggestions_result is not None else []
        ),
        source_enrichment_opportunities=(
            suggestions_result.source_enrichment_opportunities
            if suggestions_result is not None else []
        ),
    )

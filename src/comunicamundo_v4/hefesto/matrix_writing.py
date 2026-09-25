"""Section-sized drafting and review using subject claims from the matrix."""
from __future__ import annotations

from collections.abc import Awaitable, Callable
from hashlib import sha256
import json
from math import ceil
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from .contracts import (
    ContentReviewProposal, FinalTextProposal, PreservationReviewProposal,
    ProductRun,
)


class SectionText(BaseModel):
    model_config = ConfigDict(extra="forbid")
    body: str = Field(min_length=1, max_length=12_000)


def _hash(value: str) -> str:
    return sha256(value.encode()).hexdigest()


async def _cached(
    run: ProductRun, repository: object, task: str, prompt: str,
    schema: type[BaseModel],
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
    output_tokens: int,
) -> BaseModel:
    digest = _hash(prompt)
    key = _hash(task + ":" + digest)
    value = await repository.matrix_work(
        run.execution_run_id, run.editorial_round, task, key, digest,
    )
    if value is not None:
        return schema.model_validate(value)
    result = await call(task, prompt, schema, output_tokens)
    await repository.save_matrix_work(
        run.execution_run_id, run.editorial_round, task, key, digest,
        result.model_dump(mode="json"),
    )
    return result


def _prioritize(
    claims: list[dict[str, Any]], roles: list[tuple[str, str]], word_target: int,
) -> list[dict[str, Any]]:
    by_id = {item["claim_id"]: item for item in claims}
    rank = {"core": 0, "tension": 1, "qualification": 2, "context": 3}
    ordered = sorted(
        ((rank.get(role, 4), by_id[claim_id])
         for claim_id, role in roles if claim_id in by_id),
        key=lambda pair: (
            pair[0],
            -len({e["source_ordinal"] for e in pair[1]["evidence"]}),
            pair[1]["claim_id"],
        ),
    )
    limit = min(40, max(4, word_target // 28))
    chosen: list[dict[str, Any]] = []
    used: set[str] = set()
    # Preserve disagreements and source diversity before filling with core claims.
    for wanted in ("contradiction", "limit", "qualification"):
        for _, claim in ordered:
            if claim["kind"] == wanted and claim["claim_id"] not in used:
                chosen.append(claim)
                used.add(claim["claim_id"])
                break
    for _, claim in ordered:
        if len(chosen) >= limit:
            break
        if claim["claim_id"] not in used:
            chosen.append(claim)
            used.add(claim["claim_id"])
    return chosen[:limit]


async def draft_from_matrix(
    run: ProductRun,
    repository: object,
    call: Callable[[str, str, type[BaseModel], int], Awaitable[BaseModel]],
) -> FinalTextProposal:
    project = run.editorial_project.proposal
    claims = await repository.matrix_claims(run.execution_run_id)
    roles = await repository.matrix_subject_claim_roles(
        run.execution_run_id, run.editorial_round,
    )
    total_words = round(
        project.duration_minutes
        * (140 if run.request.brief.duration_minutes <= 0 else
           140 * 1)
    )
    # Product-specific rate mirrors the existing writer's duration guidance.
    rates = {"report": 130, "video": 135, "voice-over": 135,
             "voiceover": 135, "podcast": 145, "presentation": 120}
    total_words = project.duration_minutes * rates.get(
        run.request.brief.product.strip().lower(), 140,
    )
    minutes = sum(item.allocated_minutes for item in project.source_supported_subjects)
    sections: list[str] = []
    for index, subject in enumerate(project.source_supported_subjects):
        word_target = max(
            80, round(total_words * subject.allocated_minutes / minutes),
        )
        chosen = _prioritize(claims, roles.get(index, []), word_target)
        if not chosen:
            raise ValueError(f"matrix has no routed claims for subject {subject.title}")
        chunk_count = max(1, ceil(word_target / 320))
        packets: list[list[dict[str, Any]]] = [[] for _ in range(chunk_count)]
        for position, claim in enumerate(chosen):
            packets[min(chunk_count - 1, position * chunk_count // len(chosen))].append(claim)
        pieces: list[str] = []
        for part, packet in enumerate(packets):
            if not packet:
                continue
            target = max(90, round(word_target / chunk_count))
            evidence = [{
                "id": item["claim_id"], "kind": item["kind"],
                "text": item["statement"], "origin": item["origin"],
                "sources": sorted({e["source_ordinal"] for e in item["evidence"]}),
            } for item in packet]
            prompt = (
                f"Write one coherent part ({part + 1}/{chunk_count}) of this "
                f"approved subject in {run.request.brief.output_language}. "
                f"Aim for about {target} words. Respect the Narrative Axis. "
                "Use the listed contributions; distinguish model knowledge "
                "from documentary support, retain limits and contradictions. "
                "No invented facts, source names, quotes or source ordinals. "
                "Do not introduce an unrelated subject. Avoid repeated "
                "openings or conclusions between parts.\n"
                f"BRIEF: {run.request.brief.model_dump_json()}\n"
                f"AXIS: {project.narrative_axis.model_dump_json()}\n"
                f"SUBJECT: {subject.model_dump_json()}\n"
                f"PREVIOUS ENDING: {pieces[-1][-360:] if pieces else ''}\n"
                f"CONTRIBUTIONS: {json.dumps(evidence, ensure_ascii=False)}"
            )
            draft = await _cached(
                run, repository, "matrix_section_draft", prompt,
                SectionText, call, 2_048,
            )
            review_prompt = (
                "Review this one section part against the approved subject "
                "and its contributions. Identify factual gaps, dropped "
                "qualifications, unjustified inferences and needless "
                "repetition. Return only actionable issues.\n"
                f"SUBJECT: {subject.model_dump_json()}\n"
                f"CONTRIBUTIONS: {json.dumps(evidence, ensure_ascii=False)}\n"
                f"DRAFT: {draft.body}"
            )
            review = await _cached(
                run, repository, "matrix_section_review", review_prompt,
                ContentReviewProposal, call, 1_536,
            )
            text = draft.body
            if review.issues:
                correction_prompt = (
                    "Correct only the issues listed. Preserve everything "
                    "already right. Return the complete corrected section part.\n"
                    f"DRAFT: {text}\nISSUES: {review.model_dump_json()}"
                )
                correction = await _cached(
                    run, repository, "matrix_section_correction",
                    correction_prompt, SectionText, call, 2_048,
                )
                text = correction.body
            style_prompt = (
                f"Style this section part for a {run.request.brief.product} "
                f"for {run.request.brief.audience} in "
                f"{run.request.brief.output_language}. Preserve every "
                "substantive idea, limit, fact and uncertainty. Return only "
                f"the section part, without a title.\nCONTENT: {text}"
            )
            styled = await _cached(
                run, repository, "matrix_section_style", style_prompt,
                SectionText, call, 2_048,
            )
            preservation_prompt = (
                "Compare content and styled text. Approve only when every "
                "substantive idea and qualification is preserved.\n"
                f"CONTENT: {text}\nSTYLED: {styled.body}"
            )
            preservation = await _cached(
                run, repository, "matrix_section_preservation",
                preservation_prompt, PreservationReviewProposal, call, 1_024,
            )
            if not preservation.approved:
                repair_prompt = (
                    "Repair only these substantive differences in the "
                    "styled text; keep its form where possible.\n"
                    f"CONTENT: {text}\nSTYLED: {styled.body}\n"
                    f"DIFFERENCES: {preservation.model_dump_json()}"
                )
                repaired = await _cached(
                    run, repository, "matrix_section_repair",
                    repair_prompt, SectionText, call, 2_048,
                )
                final_check = await _cached(
                    run, repository, "matrix_section_recheck",
                    f"Compare content and styled text for substantive "
                    f"preservation.\nCONTENT: {text}\nSTYLED: {repaired.body}",
                    PreservationReviewProposal, call, 1_024,
                )
                if not final_check.approved:
                    raise ValueError("matrix section failed preservation review")
                styled = repaired
            pieces.append(styled.body.strip())
        sections.append(f"{subject.title}\n\n" + "\n\n".join(pieces))
    return FinalTextProposal(
        title=project.working_title,
        body="\n\n".join(sections),
    )

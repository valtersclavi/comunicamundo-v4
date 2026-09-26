from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from uuid import uuid4

from comunicamundo_v4.hefesto.contracts import (
    EditorialBrief, EditorialProjectScaffold, EditorialSubject,
)
import comunicamundo_v4.hefesto.matrix_synthesis as matrix_synthesis
from comunicamundo_v4.hefesto.matrix_synthesis import (
    Placements, Themes, _route, _themes,
)
from comunicamundo_v4.hefesto.matrix_writing import _prioritize


class WorkRepository:
    def __init__(self) -> None:
        self.work = {}
        self.links = {}

    async def matrix_work(self, execution_id, round_number, task, unit_id, digest):
        return self.work.get((task, unit_id, digest))

    async def save_matrix_work(self, execution_id, round_number, task, unit_id, digest, payload):
        self.work[(task, unit_id, digest)] = payload

    async def replace_matrix_subject_links(self, execution_id, round_number, index, assignments):
        self.links[index] = assignments


def brief() -> EditorialBrief:
    return EditorialBrief(
        theme="Test all distinct ideas",
        approach="Preserve source contributions",
        duration_minutes=10,
        output_language="pt-BR",
        audience="Readers",
        product="article",
    )


def claims(count: int) -> list[dict]:
    return [
        {
            "claim_id": f"claim-{index}",
            "kind": "idea",
            "statement": f"Distinct finding {index}",
            "origin": "source",
            "evidence": [{"source_ordinal": 1}],
        }
        for index in range(count)
    ]


def test_themes_keep_distinct_ideas_beyond_twelve() -> None:
    async def scenario() -> None:
        repository = WorkRepository()
        seen = []

        async def call(task, prompt, schema, output_tokens):
            packet = json.loads(prompt.split("CLAIMS: ", 1)[1])
            seen.extend(item["id"] for item in packet)
            return Themes.model_validate({
                "items": [
                    {"title": item["id"], "insight": item["statement"]}
                    for item in packet
                ]
            })

        result = await _themes(repository, uuid4(), 1, brief(), claims(17), call)
        assert len(result) == 17
        assert len(seen) == 17

    asyncio.run(scenario())


def test_routing_considers_every_subject_for_every_claim() -> None:
    async def scenario() -> None:
        repository = WorkRepository()
        subjects = [
            EditorialSubject(
                title=f"Subject {index}",
                editorial_role="Develop this theme",
                depth="Full",
                allocated_minutes=1,
            )
            for index in range(17)
        ]
        packets = []

        async def call(task, prompt, schema, output_tokens):
            subject_text = prompt.split("SUBJECTS: ", 1)[1]
            subset, _ = json.JSONDecoder().raw_decode(subject_text)
            packet = json.loads(prompt.split("CLAIMS: ", 1)[1])
            packets.append((len(subset), len(packet)))
            return Placements.model_validate({
                "items": [
                    {
                        "claim_id": item["id"],
                        "subject_indexes": [subject["index"] for subject in subset],
                        "role": "core",
                    }
                    for item in packet
                ]
            })

        result = await _route(
            repository, uuid4(), 1, brief(), claims(11), subjects, call,
        )
        assert len(result) == 17
        assert all(len(value) == 11 for value in result.values())
        assert all(len(repository.links[index]) == 11 for index in range(17))
        assert max(size for size, _ in packets) <= 8
        assert max(size for _, size in packets) <= 10

    asyncio.run(scenario())


def test_writing_does_not_drop_claims_after_forty() -> None:
    source = claims(41)
    roles = [(item["claim_id"], "core") for item in source]
    result = _prioritize(source, roles, set())
    assert {item["claim_id"] for item in result} == {
        item["claim_id"] for item in source
    }


def test_scaffold_assembles_all_distinct_subjects(monkeypatch) -> None:
    async def scenario() -> None:
        repository = WorkRepository()
        source_claims = claims(17)

        async def matrix_claims(execution_id):
            return source_claims

        async def remap_matrix_subject_links(execution_id, round_number, indexes):
            return None

        repository.matrix_claims = matrix_claims
        repository.remap_matrix_subject_links = remap_matrix_subject_links

        async def route(repository, execution_id, round_number, brief, claims, subjects, call):
            return {index: list(claims) for index in range(len(subjects))}

        async def relations(*args):
            return None

        async def reading(repository, execution_id, round_number, subject, claims, call):
            return matrix_synthesis.SubjectReading(
                synthesis=subject.title, points=[subject.title],
            )

        monkeypatch.setattr(matrix_synthesis, "_route", route)
        monkeypatch.setattr(matrix_synthesis, "_relations", relations)
        monkeypatch.setattr(matrix_synthesis, "_reading", reading)

        seen_packets = []
        async def call(task, prompt, schema, output_tokens):
            if task == "matrix_themes_complete":
                packet = json.loads(prompt.split("CLAIMS: ", 1)[1])
                return Themes.model_validate({"items": [
                    {"title": item["id"], "insight": item["statement"]}
                    for item in packet
                ]})
            if task == "matrix_scaffold_part":
                packet = json.loads(prompt.split("THEMES: ", 1)[1])
                seen_packets.append(len(packet))
                return EditorialProjectScaffold.model_validate({
                    "working_title": "Complete project",
                    "editorial_promise": "Cover the distinct evidence",
                    "narrative_axis": {
                        "statement": "Preserve the whole picture",
                        "rationale": "Every contribution matters",
                    },
                    "duration_minutes": 10,
                    "source_supported_subjects": [
                        {
                            "title": item["title"],
                            "editorial_role": item["insight"],
                            "depth": item["insight"],
                            "allocated_minutes": 1,
                        }
                        for item in packet
                    ],
                    "corpus_governing_understanding": "Retain every direction",
                })
            if task.startswith("matrix_header_merge"):
                return matrix_synthesis.ProjectHeader.model_validate({
                    "working_title": "Complete project",
                    "editorial_promise": "Cover the distinct evidence",
                    "narrative_axis": {
                        "statement": "Preserve the whole picture",
                        "rationale": "Every contribution matters",
                    },
                })
            if task == "matrix_suggestions":
                return matrix_synthesis.MatrixSuggestions(
                    model_knowledge_suggestions=["One small contribution"],
                    source_enrichment_opportunities=["One documented gap"],
                )
            raise AssertionError(task)

        run = SimpleNamespace(
            execution_run_id=uuid4(),
            request=SimpleNamespace(
                brief=brief(),
                provisional_project=None,
                existing_editorial_project=None,
            ),
            editorial_project=None,
        )
        proposal = await matrix_synthesis.propose_from_matrix(
            run, repository, call,
        )
        assert len(proposal.source_supported_subjects) == 17
        assert len(proposal.editorial_corpus.units) == 17
        assert sum(seen_packets) == 17
        assert max(seen_packets) <= 8

    asyncio.run(scenario())

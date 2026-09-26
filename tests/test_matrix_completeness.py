from __future__ import annotations

import asyncio
import json
from uuid import uuid4

from comunicamundo_v4.hefesto.contracts import EditorialBrief, EditorialSubject
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

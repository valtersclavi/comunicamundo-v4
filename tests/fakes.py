from __future__ import annotations

import json
import re
from typing import Sequence
from uuid import UUID

from pydantic import BaseModel

from comunicamundo_v4.hefesto.contracts import (
    CallUsage,
    EditorialProjectProposal,
    EditorialUnderstandingProposal,
    NarrativeAxisProposal,
    Paragraph,
    ParagraphBatchProposal,
    ParagraphNote,
    ProductRun,
)
from comunicamundo_v4.hefesto.gateway import ModelCallError, ModelResult


class MemoryRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, ProductRun] = {}
        self.structures: dict[UUID, list[Paragraph]] = {}
        self.saved_notes: dict[UUID, dict[str, ParagraphNote]] = {}
        self.usage: dict[UUID, list[CallUsage]] = {}

    async def save_run(self, run: ProductRun) -> None:
        self.runs[run.execution_run_id] = run

    async def get_run(self, execution_run_id: UUID) -> ProductRun | None:
        return self.runs.get(execution_run_id)

    async def save_structure(self, execution_run_id: UUID, paragraphs: Sequence[Paragraph]) -> None:
        self.structures[execution_run_id] = list(paragraphs)

    async def save_note(self, execution_run_id: UUID, note: ParagraphNote) -> None:
        self.saved_notes.setdefault(execution_run_id, {}).setdefault(note.paragraph_id, note)

    async def notes(self, execution_run_id: UUID) -> list[ParagraphNote]:
        structure = self.structures[execution_run_id]
        values = self.saved_notes.get(execution_run_id, {})
        return [values[item.paragraph_id] for item in structure if item.paragraph_id in values]

    async def record_usage(self, execution_run_id: UUID, project_id: UUID, usage: CallUsage) -> None:
        self.usage.setdefault(execution_run_id, []).append(usage)

    async def usage_report(self, execution_run_id: UUID) -> dict[str, object]:
        calls = self.usage.get(execution_run_id, [])
        return {"call_count": len(calls), "total_tokens": sum(item.total_tokens for item in calls)}


class FakeGateway:
    def __init__(self, *, fail_understanding_once: bool = False) -> None:
        self.calls: list[dict[str, object]] = []
        self.fail_understanding_once = fail_understanding_once

    async def generate(
        self,
        *,
        task_type: str,
        prompt: str,
        schema: type[BaseModel],
        system_instruction: str,
        max_output_tokens: int,
        thinking_tokens: int,
    ) -> ModelResult:
        self.calls.append({"task_type": task_type, "prompt": prompt, "schema": schema.__name__})
        usage = CallUsage(
            task_type=task_type, prompt_tokens=10, output_tokens=5,
            reasoning_tokens=1, total_tokens=16, latency_ms=2,
            rated_cost_usd="0.000015800", status="succeeded",
        )
        if task_type == "paragraph_batch":
            numbers = [int(item) for item in re.findall(r'"paragraph_number":\s*(\d+)', prompt)]
            value = ParagraphBatchProposal.model_validate(
                {
                    "notes": [
                        {
                            "paragraph_number": item,
                            "observations": [
                                {
                                    "observation": f"Meaning of paragraph {item}",
                                    "tags": ["free-tag-not-in-an-ontology"],
                                    "evidence_sentence_ordinals": [1],
                                },
                                {
                                    "observation": f"A distinct nuance from paragraph {item}",
                                    "tags": ["second-observation"],
                                    "evidence_sentence_ordinals": [1],
                                },
                            ],
                            "brief_relevance": "Potentially relevant without erasing the source.",
                        }
                        for item in numbers
                    ]
                }
            )
        elif task_type == "editorial_understanding":
            if self.fail_understanding_once:
                self.fail_understanding_once = False
                raise ModelCallError("synthetic synthesis failure")
            value = EditorialUnderstandingProposal(
                synthesis="Integrated reading guided by the brief and requested duration.",
                central_ideas=["A central idea"],
                relations_and_tensions=["A meaningful tension"],
                nuances_and_limits=["A limit"],
                editorial_possibilities=["A viable editorial direction"],
                supporting_paragraphs=[
                    {"source_ordinal": 1, "paragraph_ordinal": 1},
                    {"source_ordinal": 1, "paragraph_ordinal": 2},
                ],
            )
        elif task_type == "narrative_axis":
            value = NarrativeAxisProposal(
                statement="A concise intellectual direction for the piece.",
                rationale="It selects what matters for the brief.",
                supporting_paragraphs=[{"source_ordinal": 1, "paragraph_ordinal": 1}],
            )
        elif task_type == "editorial_project":
            duration = int(re.search(r'"duration_minutes":\s*(\d+)', prompt).group(1))
            value = EditorialProjectProposal.model_validate(
                {
                    "working_title": "Working title",
                    "editorial_promise": "A concrete promise",
                    "duration_minutes": duration,
                    "source_supported_subjects": [
                        {
                            "title": "Core subject",
                            "editorial_role": "Develop the Axis",
                            "depth": "Enough depth for the requested duration",
                            "allocated_minutes": duration,
                            "supporting_paragraphs": [
                                {"source_ordinal": 1, "paragraph_ordinal": 1}
                            ],
                        }
                    ],
                    "model_knowledge_suggestions": ["Optional model suggestion"],
                    "source_enrichment_opportunities": ["Additional source opportunity"],
                }
            )
        else:
            raise AssertionError(task_type)
        assert isinstance(value, schema)
        return ModelResult(value=value, usage=usage)

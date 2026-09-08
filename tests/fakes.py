from __future__ import annotations

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
    ParagraphNote,
    ParagraphNoteProposal,
    ProductRun,
)
from comunicamundo_v4.hefesto.gateway import ModelCallError, ModelResult


class MemoryRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, ProductRun] = {}
        self.structures: dict[UUID, list[Paragraph]] = {}
        self.saved_notes: dict[UUID, dict[str, ParagraphNote]] = {}
        self.usage: dict[UUID, list[CallUsage]] = {}
        self.stages: dict[UUID, list[dict[str, object]]] = {}
        self.receipts: dict[UUID, dict[str, object]] = {}

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

    async def record_stages(self, execution_run_id: UUID, stages: Sequence[dict[str, object]]) -> None:
        known = {item["stage"]: item for item in self.stages.get(execution_run_id, [])}
        known.update({item["stage"]: dict(item) for item in stages})
        self.stages[execution_run_id] = list(known.values())

    async def stage_report(self, execution_run_id: UUID) -> list[dict[str, object]]:
        return self.stages.get(execution_run_id, [])

    async def record_product_receipt(self, execution_run_id: UUID, payload: dict[str, object]) -> None:
        self.receipts[execution_run_id] = payload


class FakeGateway:
    def __init__(self, *, fail_understanding_times: int = 0) -> None:
        self.calls: list[dict[str, object]] = []
        self.understanding_failures_remaining = fail_understanding_times

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
        if task_type == "paragraph_digest":
            value = ParagraphNoteProposal.model_validate(
                {
                    "observations": [
                        {
                            "observation": "Meaning of the paragraph",
                            "tags": ["free-tag-not-in-an-ontology"],
                        },
                        {
                            "observation": "A distinct nuance from the paragraph",
                            "tags": ["second-observation"],
                        },
                    ],
                    "brief_relevance": "Potentially relevant without erasing the source.",
                }
            )
        elif task_type == "editorial_understanding":
            if self.understanding_failures_remaining:
                self.understanding_failures_remaining -= 1
                raise ModelCallError("synthetic synthesis failure")
            value = EditorialUnderstandingProposal(
                synthesis="Integrated reading guided by the brief and requested duration.",
                central_ideas=["A central idea"],
                relations_and_tensions=["A meaningful tension"],
                nuances_and_limits=["A limit"],
                editorial_possibilities=["A viable editorial direction"],
            )
        elif task_type == "narrative_axis":
            value = NarrativeAxisProposal(
                statement="A concise intellectual direction for the piece.",
                rationale="It selects what matters for the brief.",
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

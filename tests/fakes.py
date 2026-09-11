from __future__ import annotations

import re
from collections.abc import Sequence
from math import ceil
from typing import Any
from uuid import UUID

from pydantic import BaseModel

from comunicamundo_v4.hefesto.contracts import (
    CallUsage,
    ContentReviewProposal,
    EditorialCondensation,
    EditorialCondensationProposal,
    EditorialProjectProposal,
    FinalTextProposal,
    Paragraph,
    ProductRun,
    PreservationReviewProposal,
    SelectionBatch,
    SelectionFlags,
)
from comunicamundo_v4.hefesto.gateway import ModelCallError, ModelResult


class MemoryRepository:
    def __init__(self) -> None:
        self.runs: dict[UUID, ProductRun] = {}
        self.structures: dict[UUID, list[Paragraph]] = {}
        self.saved_selections: dict[UUID, dict[str, SelectionBatch]] = {}
        self.saved_condensations: dict[UUID, dict[str, EditorialCondensation]] = {}
        self.usage: dict[UUID, list[CallUsage]] = {}
        self.stages: dict[UUID, list[dict[str, object]]] = {}
        self.receipts: dict[UUID, dict[str, object]] = {}

    async def save_run(self, run: ProductRun) -> None:
        self.runs[run.execution_run_id] = run

    async def get_run(self, execution_run_id: UUID) -> ProductRun | None:
        return self.runs.get(execution_run_id)

    async def save_structure(
        self, execution_run_id: UUID, paragraphs: Sequence[Paragraph]
    ) -> None:
        self.structures[execution_run_id] = list(paragraphs)

    async def save_selection_batch(
        self, execution_run_id: UUID, batch: SelectionBatch
    ) -> None:
        self.saved_selections.setdefault(execution_run_id, {}).setdefault(
            batch.batch_id, batch
        )

    async def selection_batches(
        self, execution_run_id: UUID
    ) -> list[SelectionBatch]:
        return sorted(
            self.saved_selections.get(execution_run_id, {}).values(),
            key=lambda item: (item.source_ordinal, item.first_passage_ordinal),
        )

    async def save_condensation(
        self, execution_run_id: UUID, unit: EditorialCondensation
    ) -> None:
        self.saved_condensations.setdefault(execution_run_id, {}).setdefault(
            unit.unit_id, unit
        )

    async def condensations(
        self, execution_run_id: UUID
    ) -> list[EditorialCondensation]:
        return sorted(
            self.saved_condensations.get(execution_run_id, {}).values(),
            key=lambda item: (item.level, item.ordinal),
        )

    async def work_report(self, execution_run_id: UUID) -> dict[str, object]:
        selections = await self.selection_batches(execution_run_id)
        selected = {
            (item.source_ordinal, ordinal)
            for item in selections
            for ordinal in (
                *item.flags.relevant, *item.flags.central, *item.flags.uncertain
            )
        }
        return {
            "selection_batches": len(selections),
            "selected_passages": len(selected),
            "condensation_units": len(await self.condensations(execution_run_id)),
        }

    async def record_usage(
        self, execution_run_id: UUID, project_id: UUID, usage: CallUsage
    ) -> None:
        self.usage.setdefault(execution_run_id, []).append(usage)

    async def usage_report(self, execution_run_id: UUID) -> dict[str, object]:
        calls = self.usage.get(execution_run_id, [])
        return {
            "call_count": len(calls),
            "total_tokens": sum(item.total_tokens for item in calls),
        }

    async def record_stages(
        self, execution_run_id: UUID, stages: Sequence[dict[str, object]]
    ) -> None:
        known = {
            item["stage"]: item for item in self.stages.get(execution_run_id, [])
        }
        known.update({item["stage"]: dict(item) for item in stages})
        self.stages[execution_run_id] = list(known.values())

    async def stage_report(
        self, execution_run_id: UUID
    ) -> list[dict[str, object]]:
        return self.stages.get(execution_run_id, [])

    async def record_product_receipt(
        self, execution_run_id: UUID, payload: dict[str, object]
    ) -> None:
        self.receipts[execution_run_id] = payload


class FakeGateway:
    def __init__(
        self,
        *,
        fail_task: str | None = None,
        fail_times: int = 0,
        token_divisor: int = 4,
        word_counts: dict[str, int] | None = None,
        reject_verification_times: int = 0,
    ) -> None:
        self.calls: list[dict[str, object]] = []
        self.counted: list[str] = []
        self.fail_task = fail_task
        self.failures_remaining = fail_times
        self.token_divisor = token_divisor
        self.word_counts = word_counts or {}
        self.reject_verification_times = reject_verification_times

    async def count_tokens(self, text: str) -> int:
        self.counted.append(text)
        return max(1, ceil(len(text) / self.token_divisor))

    async def generate(
        self,
        *,
        task_type: str,
        prompt: str,
        schema: type[BaseModel],
        system_instruction: str,
        max_output_tokens: int,
        thinking_tokens: int,
    ) -> ModelResult[Any]:
        self.calls.append(
            {"task_type": task_type, "prompt": prompt, "schema": schema.__name__}
        )
        usage = CallUsage(
            task_type=task_type,
            prompt_tokens=max(1, ceil(len(prompt) / self.token_divisor)),
            output_tokens=5,
            reasoning_tokens=1,
            total_tokens=max(1, ceil(len(prompt) / self.token_divisor)) + 6,
            latency_ms=2,
            rated_cost_usd="0.000015800",
            status="succeeded",
        )
        if task_type == self.fail_task and self.failures_remaining:
            self.failures_remaining -= 1
            raise ModelCallError(f"synthetic {task_type} failure")
        if task_type == "source_selection":
            allowed = [
                int(value)
                for value in re.findall(r"\[passage (\d+);", prompt.split("CORE PASSAGES:", 1)[1].split("NEXT CONTEXT", 1)[0])
            ]
            value = SelectionFlags(
                relevant=allowed[1:] if len(allowed) > 1 else allowed,
                central=allowed[:1] if len(allowed) > 1 else [],
            )
        elif task_type in {
            "editorial_comprehension_part", "editorial_comprehension_merge"
        }:
            value = EditorialCondensationProposal(
                reading="A compact editorial reading preserving source tensions and limits."
            )
        elif task_type == "editorial_project":
            duration = int(re.search(r'"duration_minutes":\s*(\d+)', prompt).group(1))
            value = EditorialProjectProposal.model_validate(
                {
                    "working_title": "Working title",
                    "editorial_promise": "A concrete promise",
                    "narrative_axis": {
                        "statement": "A concise intention of message for the complete piece.",
                        "rationale": "It carries the user's intention through the sources.",
                    },
                    "duration_minutes": duration,
                    "source_supported_subjects": [
                        {
                            "title": "Core subject",
                            "editorial_role": "Develop the Narrative Axis",
                            "depth": "Enough depth for the requested duration",
                            "allocated_minutes": duration,
                        }
                    ],
                    "editorial_corpus": {
                        "governing_understanding": (
                            "The sources support one integrated understanding."
                        ),
                        "units": [
                            {
                                "title": "Core subject",
                                "synthesis": "The complete source-grounded synthesis.",
                                "essential_points": [
                                    "The central supported point",
                                    "The relevant qualification",
                                ],
                                "tensions": ["A meaningful source tension"],
                                "qualifications": ["Do not overstate causality"],
                                "evidence": [
                                    {
                                        "source_ordinals": [1, 2],
                                        "contribution": (
                                            "Both sources support and qualify the point."
                                        ),
                                    }
                                ],
                            }
                        ],
                        "forbidden_inferences": [
                            "Do not turn association into causation"
                        ],
                    },
                    "model_knowledge_suggestions": ["Optional model suggestion"],
                    "source_enrichment_opportunities": [
                        "Additional source opportunity"
                    ],
                }
            )
        elif task_type in {
            "content_draft",
            "content_revision",
            "content_contract_correction",
            "product_styling",
            "product_style_correction",
        }:
            match = re.search(r"(?:approximately|target is) (\d+) words", prompt)
            words = self.word_counts.get(
                task_type, int(match.group(1)) if match else 1_120
            )
            value = FinalTextProposal(
                title="Finished title",
                body=" ".join(["conteúdo"] * words),
            )
        elif task_type == "content_review":
            value = ContentReviewProposal(
                approved=True,
                summary="The content respects the approved editorial state.",
                issues=[],
            )
        elif task_type in {"final_verification", "final_reverification"}:
            rejected = self.reject_verification_times > 0
            if rejected:
                self.reject_verification_times -= 1
            value = PreservationReviewProposal(
                approved=not rejected,
                summary=(
                    "The styled text changed content."
                    if rejected
                    else "The styled text preserves the closed content."
                ),
                differences=["A qualification was lost"] if rejected else [],
            )
        else:
            raise AssertionError(task_type)
        assert isinstance(value, schema)
        return ModelResult(value=value, usage=usage)

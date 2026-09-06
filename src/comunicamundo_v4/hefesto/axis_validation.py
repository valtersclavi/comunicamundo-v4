"""Canonical Narrative Axis validation shared by the PostgreSQL pipeline."""
from __future__ import annotations

import re

from .contracts import NarrativeAxisEvidenceReview


_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")


def count_sentences(text: str) -> int:
    stripped = " ".join(text.split())
    if not stripped:
        return 0
    return len([part for part in _SENTENCE_SPLIT.split(stripped) if part.strip()])


def validate_axis_statement(statement: str) -> str:
    normalized = " ".join(statement.split())
    if not normalized:
        raise ValueError("Narrative Axis cannot be empty")
    sentences = count_sentences(normalized)
    if sentences < 1 or sentences > 2:
        raise ValueError(f"Narrative Axis must be 1–2 sentences, got {sentences}")
    return normalized


def apply_evidence_strength_review(candidate: str, review: NarrativeAxisEvidenceReview) -> str:
    candidate = validate_axis_statement(candidate)
    final = validate_axis_statement(review.final_statement)
    return final if review.revision_required else candidate

"""Fast, deterministic evidence selection for V2 production."""
from __future__ import annotations

import re
import unicodedata
from collections import defaultdict
from collections.abc import Sequence

from .contracts import EditorialProject, EditorialProjectStatus
from .digestion_contracts import EvidenceMatrix, SourceDossier
from .production_v2_contracts import (
    EvidencePacketFinding,
    ProductionEvidenceBundle,
    SubjectEvidencePacket,
)


_WORD = re.compile(r"[a-z0-9]+")


class ProductionEvidenceBuilder:
    """Build fixed-size subject packets without embeddings or model calls."""

    def __init__(
        self,
        *,
        max_total_subject_chars: int = 80_000,
        max_subject_chars: int = 18_000,
        max_shared_chars: int = 28_000,
        findings_per_source: int = 2,
    ) -> None:
        if min(max_total_subject_chars, max_subject_chars, max_shared_chars) <= 0:
            raise ValueError("production evidence budgets must be positive")
        if findings_per_source <= 0:
            raise ValueError("each source must have a positive finding ceiling")
        self._max_total = max_total_subject_chars
        self._max_subject = max_subject_chars
        self._max_shared = max_shared_chars
        self._per_source = findings_per_source

    def build(
        self,
        *,
        execution_run_id,
        project: EditorialProject,
        matrix: EvidenceMatrix,
        dossiers: Sequence[SourceDossier],
    ) -> ProductionEvidenceBundle:
        if project.status is not EditorialProjectStatus.APPROVED:
            raise ValueError("production evidence requires an approved Editorial Project")
        if matrix.execution_run_id != execution_run_id or matrix.project_id != project.project_id:
            raise ValueError("evidence matrix belongs to another production run")
        if [item.source_id for item in dossiers] != matrix.source_ids:
            raise ValueError("production dossiers must preserve the matrix source order")
        if [item.content_sha256 for item in dossiers] != matrix.dossier_sha256s:
            raise ValueError("production dossiers disagree with matrix hashes")
        per_subject_budget = min(
            self._max_subject,
            max(2_000, self._max_total // len(project.core_subjects)),
        )
        packets = [
            self._packet(subject, matrix, dossiers, max_chars=per_subject_budget)
            for subject in project.core_subjects
        ]
        return ProductionEvidenceBundle(
            execution_run_id=execution_run_id,
            project_id=project.project_id,
            editorial_project_id=project.editorial_project_id,
            evidence_matrix_sha256=matrix.content_sha256,
            dossier_sha256s=matrix.dossier_sha256s,
            shared_context=self._shared_context(matrix)[: self._max_shared],
            subject_packets=packets,
        )

    def _packet(self, subject, matrix, dossiers, *, max_chars: int) -> SubjectEvidencePacket:
        query = _terms(f"{subject.title} {subject.development}")
        by_source: dict[object, list[tuple[int, object, SourceDossier]]] = defaultdict(list)
        for dossier in dossiers:
            for finding in dossier.findings:
                rendered = " ".join(
                    [finding.finding, *finding.qualifications, *dossier.distinctive_contributions]
                )
                score = len(query & _terms(rendered))
                by_source[dossier.source_id].append((score, finding, dossier))
        selected: list[tuple[int, object, SourceDossier]] = []
        for dossier in dossiers:
            ranked = sorted(
                by_source.get(dossier.source_id, []),
                key=lambda item: (-item[0], item[1].finding),
            )
            selected.extend(ranked[: self._per_source])
        # Keep source representation bounded and predictable: one pass across
        # source order before a second finding from any source.
        interleaved: list[tuple[int, object, SourceDossier]] = []
        for rank in range(self._per_source):
            for dossier in dossiers:
                candidates = [item for item in selected if item[2].source_id == dossier.source_id]
                if rank < len(candidates):
                    interleaved.append(candidates[rank])

        findings: list[EvidencePacketFinding] = []
        rendered_parts = [
            f"SUBJECT: {subject.title}",
            f"DEVELOPMENT: {subject.development}",
        ]
        for score, finding, dossier in interleaved:
            if len(findings) >= 100:
                break
            packet_finding = EvidencePacketFinding(
                source_id=dossier.source_id,
                source_title=dossier.source_title,
                finding=finding.finding,
                causal_status=finding.causal_status.value,
                evidence_strength=finding.evidence_strength.value,
                qualifications=finding.qualifications,
                anchors=finding.anchors,
                relevance_score=score,
            )
            block = _render_finding(packet_finding)
            if len("\n\n".join([*rendered_parts, block])) > max_chars:
                continue
            findings.append(packet_finding)
            rendered_parts.append(block)
        limits = _relevant_limits(query, matrix, dossiers)
        limit_block = "LIMITS:\n" + "\n".join(f"- {item}" for item in limits)
        if len("\n\n".join([*rendered_parts, limit_block])) <= max_chars:
            rendered_parts.append(limit_block)
        if not findings:
            rendered_parts.append(
                "No dossier finding cleared the packet budget; use the shared matrix context and do not invent evidence."
            )
        rendered = "\n\n".join(rendered_parts)
        return SubjectEvidencePacket(
            subject_id=subject.subject_id,
            title=subject.title,
            development=subject.development,
            findings=findings,
            source_limits=limits,
            rendered_context=rendered[:max_chars],
        )

    def _shared_context(self, matrix: EvidenceMatrix) -> str:
        fields = [
            ("INTEGRATED SYNTHESIS", [matrix.integrated_synthesis]),
            ("CENTRAL IDEAS", matrix.central_ideas),
            ("RELATIONS AND TENSIONS", matrix.relations_and_tensions),
            ("NUANCES", matrix.nuances),
            ("SOURCE LIMITS", matrix.source_limits),
            (
                "CAUSALITY ASSESSMENTS",
                [
                    f"{item.claim} [{item.causal_status.value}] — {item.explanation}"
                    for item in matrix.causality_assessments
                ],
            ),
            (
                "METHODOLOGICAL LIMITS",
                [f"{item.limitation} — {item.consequence}" for item in matrix.methodological_limits],
            ),
            (
                "DIVERGENCES",
                [f"{item.question_or_tension} — {item.reconciliation}" for item in matrix.divergences],
            ),
            (
                "SOURCE DOMINANCE",
                [matrix.dominance_assessment.explanation],
            ),
        ]
        return "\n\n".join(
            f"{heading}:\n" + "\n".join(f"- {item}" for item in values)
            for heading, values in fields
            if values
        )


def _terms(value: str) -> set[str]:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
    return {word for word in _WORD.findall(normalized) if len(word) >= 4}


def _render_finding(item: EvidencePacketFinding) -> str:
    anchors = "; ".join(
        f"section={anchor.section_id} locator={anchor.locator!r} excerpt={anchor.excerpt!r}"
        for anchor in item.anchors
    )
    qualifications = " | ".join(item.qualifications) or "none"
    return (
        f"SOURCE {item.source_id} — {item.source_title or 'untitled'}\n"
        f"Finding: {item.finding}\n"
        f"Causality: {item.causal_status}; strength: {item.evidence_strength}\n"
        f"Qualifications: {qualifications}\n"
        f"Anchors: {anchors}"
    )


def _relevant_limits(query: set[str], matrix: EvidenceMatrix, dossiers: Sequence[SourceDossier]) -> list[str]:
    candidates = [
        *matrix.source_limits,
        *[item.limitation for item in matrix.methodological_limits],
        *[limit for dossier in dossiers for limit in dossier.limitations],
    ]
    unique = list(dict.fromkeys(item.strip() for item in candidates if item.strip()))
    return sorted(unique, key=lambda item: (-len(query & _terms(item)), item))[:20]

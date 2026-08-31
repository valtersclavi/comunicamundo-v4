from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from comunicamundo_v4.hefesto.digestion_contracts import (
    CausalStatus,
    DominanceAssessment,
    EditorialUnderstandingV2,
    EvidenceMatrix,
    EvidenceMatrixProposal,
    SourceBalanceMetric,
    CausalityAssessment,
)
from comunicamundo_v4.hefesto.source_digestion import editorial_understanding_from_matrix
from comunicamundo_v4.hefesto.execution_contracts import text_sha256


def _proposal(source_id):
    return EvidenceMatrixProposal(
        central_ideas=["Effects differ by adolescent and context."],
        relations_and_tensions=["Population averages can hide vulnerable subgroups."],
        nuances=["Timing and type of use matter."],
        source_limits=["Most evidence does not identify causality."],
        editorial_opportunities=["Explain practical risk without deterministic claims."],
        causality_assessments=[
            CausalityAssessment(
                claim="Use is associated with wellbeing.",
                source_ids=[source_id],
                causal_status=CausalStatus.CORRELATIONAL,
                explanation="The design does not isolate causal direction.",
            )
        ],
        dominance_assessment=DominanceAssessment(
            long_source_dominance_detected=False,
            explanation="Equal-ceiling dossiers remain balanced.",
        ),
        integrated_synthesis="The evidence supports a conditional, non-deterministic account.",
    )


def _matrix():
    source_id = uuid4()
    proposal = _proposal(source_id)
    matrix = EvidenceMatrix(
        **proposal.model_dump(),
        execution_run_id=uuid4(),
        project_id=uuid4(),
        source_fingerprint=text_sha256("sources"),
        source_ids=[source_id],
        dossier_sha256s=[text_sha256("dossier")],
        source_balance=[
            SourceBalanceMetric(
                source_id=source_id,
                raw_token_share=1,
                dossier_character_share=1,
                finding_share=1,
                compression_ratio=0.1,
            )
        ],
        model="test-model",
    )
    return matrix


def test_matrix_rejects_a_model_reference_to_an_unknown_source() -> None:
    matrix = _matrix()
    unknown = uuid4()
    data = matrix.model_dump()
    data["causality_assessments"][0]["source_ids"] = [unknown]

    with pytest.raises(ValidationError, match="unknown source"):
        EvidenceMatrix.model_validate(data)


def test_matrix_is_the_understanding_without_an_additional_model_call() -> None:
    matrix = _matrix()
    understanding = editorial_understanding_from_matrix(matrix)

    assert isinstance(understanding, EditorialUnderstandingV2)
    assert understanding.evidence_matrix_id == matrix.evidence_matrix_id
    assert understanding.evidence_matrix_sha256 == matrix.content_sha256
    assert understanding.synthesis == matrix.integrated_synthesis
    assert understanding.central_ideas == matrix.central_ideas


def test_matrix_hash_excludes_random_identity_and_timestamp() -> None:
    first = _matrix()
    second = EvidenceMatrix.model_validate(
        {
            **first.model_dump(),
            "evidence_matrix_id": uuid4(),
            "created_at": first.created_at.replace(year=first.created_at.year - 1),
        }
    )

    assert first.content_sha256 == second.content_sha256


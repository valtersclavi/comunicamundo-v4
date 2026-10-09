"""Prompt wiring and editorial authority; fake responses do not assess literary quality."""
import pytest

from test_v8 import complete_body, review_ok, problem, confirmed
from test_v8_hierarchy import prepared, correction
from comunicamundo_v4.hefesto.v8.editorial import AUTHORSHIP, CONSISTENCY, stage_contract
from comunicamundo_v4.hefesto.v8.provider import Calls


@pytest.mark.asyncio
async def test_authorship_reaches_draft_and_writer_without_new_operations():
    body = complete_body("An original interpretation explicitly presented as interpretation.")
    st, repo, provider, editor = await prepared([
        dict(title="Work", body=body), *review_ok(),
    ])
    await editor.execute(st, Calls(repo, provider, st))

    assert AUTHORSHIP in provider.prompts[0][1]
    assert AUTHORSHIP in provider.prompts[2][1]
    assert "195 palavras" in provider.prompts[0][1]
    assert "interpretações que não estejam explicitamente formuladas nas fontes" in AUTHORSHIP
    assert "Não invente fatos, estudos, dados, testemunhos ou evidências" in AUTHORSHIP
    assert "You may expand relationships, improve explanations" in provider.prompts[2][1]
    assert "draft classifications, enumerations or sequences are mandatory" in provider.prompts[2][1]
    assert CONSISTENCY in provider.prompts[4][1]
    assert st.work.body == body and st.status == "text_ready"
    assert [s.stage_id for s in st.editorial_stages] == [
        "completeness", "consistency", "narrative", "style",
    ]
    assert all(s.status == "approved" and not s.attempts and not s.evidence
               for s in st.editorial_stages)
    assert [op["stage"] for op in repo.ops.values()] == [
        "intellectual-draft", "editorial-project", "write",
        "completeness-diagnose", "consistency-diagnose",
        "narrative-diagnose", "style-diagnose",
    ]
    assert len(provider.prompts) == 7
    for _, system in provider.prompts[3:]:
        assert AUTHORSHIP not in system  # Full creative authority belongs to the authors.
        assert "Approve directly when there is no concrete defect" in system
    assert "not conformity to documentary organization" in provider.prompts[5][1]
    assert "consolidated narrative order" in provider.prompts[6][1]


@pytest.mark.parametrize("role", ["diagnose", "correct", "confirm", "reconsider"])
def test_consistency_contract_protects_originality_and_factual_review_in_every_role(role):
    contract = stage_contract(2, role)
    assert CONSISTENCY in contract
    assert "Uma interpretação autoral original não constitui defeito" in contract
    assert "passagem documental equivalente" in contract
    assert "erros factuais concretos" in contract
    assert "contradições efetivas" in contract
    assert "conclusões injustificadas" in contract
    assert "somente quando houver dúvida factual específica e relevante" in contract
    assert "Não reabra escolhas narrativas aprovadas por mera preferência editorial" in contract


@pytest.mark.asyncio
async def test_specific_factual_doubt_still_supports_correction_and_confirmation():
    text = "The observation reports correlation, not an established cause.\n\nUnrelated evidence."
    body = complete_body() + "\n\nThe observation establishes causality."
    st, repo, provider, editor = await prepared(text=text)
    reference = dict(source_id=str(st.sources[0].source_id),
                     location_id=st.sources[0].locations[0].location_id)
    provider.outputs = [
        dict(title="Work", body=body), review_ok()[0],
        dict(evidence_questions=[dict(question="Does this observation establish causality?",
                                      references=[reference])]),
        dict(problems=[problem(passages=["p2"])]),
        correction("The observation reports correlation; the cause remains an interpretation."),
        confirmed(), *review_ok()[2:],
    ]
    await editor.execute(st, Calls(repo, provider, st))
    stage = st.editorial_stages[1]
    assert st.status == "text_ready" and stage.status == "approved"
    assert len(stage.evidence) == 1 and len(stage.attempts) == 1
    assert "the cause remains an interpretation" in st.work.body
    calls = [(prompt, system) for prompt, system in provider.prompts if "STAGE 2:" in system]
    assert len(calls) == 4  # Existing diagnosis, addressed consultation, correction, confirmation.
    assert all(CONSISTENCY in system for _, system in calls)
    assert text.split("\n\n")[0] in calls[1][0]
    assert all("Unrelated evidence." not in prompt for prompt, _ in calls)


@pytest.mark.asyncio
async def test_existing_extent_completion_keeps_authorship_and_approved_conception():
    st, repo, provider, editor = await prepared([
        dict(title="Work", body="Valid opening.\n\nIncomplete explanation."),
        correction(complete_body()), *review_ok(),
    ])
    await editor.execute(st, Calls(repo, provider, st))
    completions = [(prompt, system) for prompt, system in provider.prompts if "ACTUAL WORDS:" in prompt]
    assert len(completions) == 1
    prompt, system = completions[0]
    assert AUTHORSHIP in system
    assert "approved conception, including deeper relationships and explanations" in prompt
    assert "Do not introduce unrelated themes, invented evidence or padding" in prompt
    assert st.status == "text_ready" and len(st.writing_checkpoint["attempts"]) == 1

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto.contracts import (
    EDITORIAL_PROJECT_CONTRACT_VERSION,
    EDITORIAL_PROJECT_PROMPT_VERSION,
    AxisAction,
    AxisFeedbackDecisionState,
    EditorialImpactKind,
    EditorialProjectProposal,
    EditorialSubjectProposal,
    EnrichmentResolutionChoice,
    NarrativeAxisStatus,
    PlanningRunState,
    SourceLineageState,
    SupportKind,
    TreatmentScale,
)
from comunicamundo_v4.hefesto.editorial_project import (
    EditorialProjectOrchestrator,
    format_carry_forwards,
    format_feedback_history,
)
from comunicamundo_v4.hefesto.persistence import (
    FirestoreEditorialRepository,
    GcsArtifactStore,
    GcpEditorialPersistence,
    GcpPersistenceSettings,
)
from comunicamundo_v4.hefesto.prompts import EDITORIAL_PROJECT_PROMPT
from comunicamundo_v4.hefesto.runtime import EditorialProjectRuntime
from fakes import FakeFirestore, FakeStorageClient, MemoryPlanningPersistence
from test_axis_feedback import (
    COMPLEMENT_FEEDBACK,
    ENRICHMENT_FEEDBACK,
    QUALIFY_FEEDBACK,
    REVISED_QUALIFYING,
    RecordingDecider,
    RecordingReviser,
    UniqueAxisUriPersistence,
    _decision_proposal,
    _orchestrator,
    _ready,
)
from test_axis_proposal import _prepare_graph_ready, _propose

PROJECT_PY = Path("src/comunicamundo_v4/hefesto/editorial_project.py").read_text()
WORKER_PY = Path("src/comunicamundo_v4/hefesto/worker.py").read_text()
FAKE_PROJECT = "fake-project"


def _prompt_text(prompt) -> str:
    template = getattr(prompt, "template", None)
    if isinstance(template, str) and template.strip():
        return template
    raise AssertionError("expected a PromptTemplate with a template string")


def _project_runtime() -> EditorialProjectRuntime:
    return EditorialProjectRuntime(
        project_llm=object(),
        project_model=FAKE_PROJECT,
        project_max_tokens=5_000,
        context_window=1_048_576,
    )


def _proposal(*, practical: str | None = "Como tornar a presença praticável no cotidiano.") -> EditorialProjectProposal:
    return EditorialProjectProposal(
        working_title="Presença compartilhada na perda",
        editorial_promise="Desenvolver a direção aprovada sem preescrever o artigo.",
        core_subjects=[
            EditorialSubjectProposal(
                title="A travessia em relação",
                development="Como a perda se atravessa em conjunto, a partir das fontes.",
                practical_dimension=practical,
            )
        ],
        llm_subjects=["Diferenças por contexto de cuidado"],
        enrichment_opportunities=["Fontes sobre a duração do processo"],
    )


class RecordingBuilder:
    def __init__(self, proposal: EditorialProjectProposal | None = None) -> None:
        self.proposal = proposal or _proposal()
        self.calls: list[dict] = []

    def build(self, **kwargs) -> EditorialProjectProposal:
        self.calls.append(kwargs)
        return self.proposal


class FailingBuilder:
    def build(self, **kwargs):
        raise RuntimeError("project failed")


def _snapshot(store, lineage, extractor, synth) -> dict:
    current = store.get_source_lineage(lineage.source_lineage_id)
    return {
        "extraction_writes": store.extraction_writes,
        "graph_writes": store.graph_writes,
        "understanding_writes": store.understanding_writes,
        "lineage_state": current.state,
        "graph_artifact_id": current.graph_artifact_id,
        "synth_materials": len(synth.materials),
        "extractor_calls": len(extractor.calls),
    }


def _assert_untouched(store, lineage, extractor, synth, before: dict) -> None:
    assert _snapshot(store, lineage, extractor, synth) == before


def _approve(store, run_id, *, builder=None, narrative_axis_id=None):
    used = builder if builder is not None else RecordingBuilder()
    result = EditorialProjectOrchestrator(store, _project_runtime(), used).execute(
        run_id,
        narrative_axis_id=narrative_axis_id,
    )
    return result, used


def _revise_proposal(**overrides):
    payload = {
        "impact": EditorialImpactKind.QUALIFIES,
        "axis_action": AxisAction.REVISE,
        "treatment_scale": TreatmentScale.INTEGRATED,
        "carry_forward_intent": None,
        "impact_rationale": "The contribution changes how the existing practice must be applied.",
        "decision_rationale": "Revise the Axis so the practice is read as relational.",
        **overrides,
    }
    return _decision_proposal(**payload)


def test_module_is_atomic_and_does_not_search_or_rebuild() -> None:
    forbidden = (
        "SimpleLLMPathExtractor",
        "from_documents",
        "build_vertex_runtime",
        "build_graph_extraction_runtime",
        "build_graph_indexing_runtime",
        "LlamaIndexChunkGraphExtractor",
        "GraphExtractionOrchestrator",
        "GraphAssemblyOrchestrator",
        "LlamaIndexUnderstandingSynthesizer",
        "persist_understanding",
        "restore_source_lineage_graph",
        "load_chunk(",
        "web_search",
        "GoogleGenAIEmbedding",
        "FinalArticle",
        "produce(",
        "AxisFeedbackOutcome",
        "AxisFeedbackUserAction",
        "CarriedEditorialDirective",
        "mention_only",
        "structural",
        "developed",
    )
    for token in forbidden:
        assert token not in PROJECT_PY
    text = _prompt_text(EDITORIAL_PROJECT_PROMPT)
    lowered = text.lower()
    assert "carry_forwards" in text
    assert "axis_feedback_history" in text
    assert "central_ideas" in text
    assert "source_limits" in text
    assert "treatment_scale" in lowered
    assert "practical_dimension" in lowered
    assert "not an outline" in lowered
    assert "approve-axis" in WORKER_PY
    assert "--planning-run-id" in WORKER_PY
    assert "build_editorial_project_runtime" in WORKER_PY
    assert "revise-axis" not in WORKER_PY
    assert "app.approve_axis" not in WORKER_PY
    assert "app.revise_axis" not in WORKER_PY


def test_revised_axis_feedback_is_consumed_and_remains_traceable() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    before = _snapshot(store, lineage, extractor, synth)
    decided = _orchestrator(
        store,
        decider=RecordingDecider(_revise_proposal()),
        reviser=RecordingReviser(REVISED_QUALIFYING),
    ).review(proposed.planning_run.planning_run_id, QUALIFY_FEEDBACK)
    assert decided.decision.axis_action is AxisAction.REVISE
    builder = RecordingBuilder()
    result, used = _approve(store, proposed.planning_run.planning_run_id, builder=builder)
    assert used is builder
    assert result.planning_run.state is PlanningRunState.PROJECT_PROPOSED
    assert result.stopped_at == "project_proposed"
    assert result.human_approval_required is True
    assert result.article_created is False
    assert result.graph_reextracted is False
    assert result.understanding_rebuilt is False
    assert result.search_performed is False
    assert result.narrative_axis.status is NarrativeAxisStatus.APPROVED
    assert result.narrative_axis.narrative_axis_id == decided.narrative_axis.narrative_axis_id
    assert result.narrative_axis.statement == decided.proposed_axis
    assert result.editorial_project.status.value == "proposed"
    assert result.editorial_project.approved_at is None
    assert result.consumed_feedback_decision is not None
    assert result.consumed_feedback_decision.feedback_decision_id == decided.decision.feedback_decision_id
    assert result.consumed_feedback_decision.state is AxisFeedbackDecisionState.DECIDED
    assert result.consumed_feedback_decision.axis_action is AxisAction.REVISE
    assert result.consumed_feedback_decision.consumed_at is not None
    assert result.consumed_feedback_decision.consumed_by_editorial_project_id == result.editorial_project.editorial_project_id
    history = builder.calls[0]["axis_feedback_history"]
    assert "revise" in history
    assert QUALIFY_FEEDBACK in history
    persisted = store.load_axis_feedback_decision(result.planning_run.artifacts.axis_feedback_decision_uri)
    assert persisted.consumed_by_editorial_project_id == result.editorial_project.editorial_project_id
    assert persisted.axis_action is AxisAction.REVISE
    assert persisted.state is AxisFeedbackDecisionState.DECIDED
    assert persisted.feedback == decided.decision.feedback
    assert persisted.editorial_project_created is False
    _assert_untouched(store, lineage, extractor, synth, before)


def test_keep_carry_forward_survives_at_treatment_scale() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    before = _snapshot(store, lineage, extractor, synth)
    intent = "Later, mention ordinary rest as a brief supporting note beside the central practice."
    decided = _orchestrator(
        store,
        decider=RecordingDecider(
            _decision_proposal(
                treatment_scale=TreatmentScale.BRIEF,
                carry_forward_intent=intent,
            )
        ),
    ).review(proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK)
    builder = RecordingBuilder()
    result, _used = _approve(store, proposed.planning_run.planning_run_id, builder=builder)
    assert result.narrative_axis.narrative_axis_id == proposed.narrative_axis.narrative_axis_id
    assert result.carry_forwards
    assert result.carry_forwards[-1].treatment_scale is TreatmentScale.BRIEF
    assert result.carry_forwards[-1].feedback_decision_id == decided.decision.feedback_decision_id
    assert "rest" in result.carry_forwards[-1].intent.lower()
    rendered = builder.calls[0]["carry_forwards"]
    assert "brief" in rendered
    assert "rest" in rendered.lower()
    assert format_carry_forwards((result.consumed_feedback_decision,)) == rendered
    assert result.consumed_feedback_decision is not None
    assert result.consumed_feedback_decision.axis_action is AxisAction.KEEP
    assert result.consumed_feedback_decision.carry_forward is not None
    assert result.consumed_feedback_decision.carry_forward.treatment_scale is TreatmentScale.BRIEF
    _assert_untouched(store, lineage, extractor, synth, before)


def test_keep_then_revise_keeps_carry_forward_and_consumes_the_chain() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    kept = _orchestrator(store, decider=RecordingDecider(_decision_proposal())).review(
        proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
    )
    revised = _orchestrator(
        store,
        decider=RecordingDecider(_revise_proposal()),
        reviser=RecordingReviser(REVISED_QUALIFYING),
    ).review(proposed.planning_run.planning_run_id, QUALIFY_FEEDBACK)
    assert revised.decision.previous_feedback_decision_id == kept.decision.feedback_decision_id
    builder = RecordingBuilder()
    result, _used = _approve(store, proposed.planning_run.planning_run_id, builder=builder)
    history = builder.calls[0]["axis_feedback_history"]
    assert str(kept.decision.feedback_decision_id) in history
    assert str(revised.decision.feedback_decision_id) in history
    assert QUALIFY_FEEDBACK in history
    assert COMPLEMENT_FEEDBACK in history
    assert result.carry_forwards
    assert any(item.feedback_decision_id == kept.decision.feedback_decision_id for item in result.carry_forwards)
    assert result.consumed_feedback_decision.feedback_decision_id == revised.decision.feedback_decision_id
    kept_persisted = store.load_axis_feedback_decision(
        kept.planning_run.artifacts.axis_feedback_decision_uri
        if kept.planning_run.artifacts.axis_feedback_decision_uri
        else result.planning_run.artifacts.axis_feedback_decision_uri
    )
    # The KEEP record remains at its own URI and is marked consumed.
    keep_uri = f"gs://memory/{proposed.planning_run.planning_run_id}/feedback/{kept.decision.feedback_decision_id}.json"
    keep_loaded = store.load_axis_feedback_decision(keep_uri)
    assert keep_loaded.consumed_by_editorial_project_id == result.editorial_project.editorial_project_id
    assert keep_loaded.axis_action is AxisAction.KEEP
    assert keep_loaded.carry_forward is not None
    assert keep_loaded.previous_feedback_decision_id is None
    revise_loaded = store.load_axis_feedback_decision(result.planning_run.artifacts.axis_feedback_decision_uri)
    assert revise_loaded.previous_feedback_decision_id == kept.decision.feedback_decision_id
    assert revise_loaded.consumed_by_editorial_project_id == result.editorial_project.editorial_project_id
    assert kept_persisted.carry_forward is not None or keep_loaded.carry_forward is not None


def test_substantial_and_integrated_carry_forwards_keep_their_scale() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    first = _orchestrator(
        store,
        decider=RecordingDecider(
            _decision_proposal(
                treatment_scale=TreatmentScale.INTEGRATED,
                carry_forward_intent="Integrate ordinary shared time into how presence is practiced.",
                treatment_rationale="The contribution belongs beside the Axis, not as a later aside.",
            )
        ),
    ).review(proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK)
    second_store_run = proposed.planning_run.planning_run_id
    # A later KEEP at substantial scale is a new feedback turn on the same Axis.
    from test_axis_feedback import SECOND_COMPLEMENT_FEEDBACK

    second = _orchestrator(
        store,
        decider=RecordingDecider(
            _decision_proposal(
                treatment_scale=TreatmentScale.SUBSTANTIAL,
                carry_forward_intent="Develop the caregiver's own process as a substantial strand.",
                treatment_rationale="The contribution must occupy real editorial space.",
            )
        ),
    ).review(second_store_run, SECOND_COMPLEMENT_FEEDBACK)
    result, builder = _approve(store, proposed.planning_run.planning_run_id)
    scales = [item.treatment_scale for item in result.carry_forwards]
    assert scales == [TreatmentScale.INTEGRATED, TreatmentScale.SUBSTANTIAL]
    assert result.carry_forwards[0].feedback_decision_id == first.decision.feedback_decision_id
    assert result.carry_forwards[1].feedback_decision_id == second.decision.feedback_decision_id
    rendered = builder.calls[0]["carry_forwards"]
    assert "integrated" in rendered
    assert "substantial" in rendered


def test_enrichment_pending_blocks_editorial_project() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    before = _snapshot(store, lineage, extractor, synth)
    orchestrator = _orchestrator(
        store,
        decider=RecordingDecider(
            _decision_proposal(
                support=SupportKind.ENRICHMENT,
                axis_action=AxisAction.HOLD_FOR_ENRICHMENT,
                carry_forward_intent=None,
                enrichment_need="Need a source that can sustain the proposed reframe.",
                support_rationale="Not currently sustainable from the current sources.",
                decision_rationale="Hold pending enrichment.",
            )
        ),
    )
    orchestrator.review(proposed.planning_run.planning_run_id, ENRICHMENT_FEEDBACK)
    builder = RecordingBuilder()
    with pytest.raises(ValueError, match="unresolved Axis enrichment"):
        _approve(store, proposed.planning_run.planning_run_id, builder=builder)
    assert builder.calls == []
    run = store.get_run(proposed.planning_run.planning_run_id)
    assert run.state is PlanningRunState.AXIS_PROPOSED
    assert run.artifacts.editorial_project_id is None
    assert store.project_writes == 0
    _assert_untouched(store, lineage, extractor, synth, before)

    orchestrator.resolve(proposed.planning_run.planning_run_id, EnrichmentResolutionChoice.ACCEPT)
    with pytest.raises(ValueError, match="accepted Axis enrichment still pending"):
        _approve(store, proposed.planning_run.planning_run_id, builder=builder)
    assert builder.calls == []
    assert store.get_run(proposed.planning_run.planning_run_id).state is PlanningRunState.AXIS_PROPOSED
    _assert_untouched(store, lineage, extractor, synth, before)


def test_superseded_and_stale_axis_are_blocked() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    stored = store.get_axis(proposed.narrative_axis.narrative_axis_id)
    store.save_axis(stored.model_copy(update={"narrative_axis": stored.narrative_axis.supersede()}))
    builder = RecordingBuilder()
    with pytest.raises(ValueError, match="superseded"):
        _approve(store, proposed.planning_run.planning_run_id, builder=builder)
    assert builder.calls == []
    assert store.project_writes == 0

    store2, proposed2, *_rest = _ready()
    run = store2.get_run(proposed2.planning_run.planning_run_id)
    store2.save_run(
        run.model_copy(
            update={"artifacts": run.artifacts.model_copy(update={"narrative_axis_id": uuid4()})}
        )
    )
    with pytest.raises(ValueError, match="does not match"):
        _approve(store2, proposed2.planning_run.planning_run_id, builder=RecordingBuilder())

    store3, proposed3, *_rest3 = _ready()
    with pytest.raises(ValueError, match="does not match the requested"):
        _approve(
            store3,
            proposed3.planning_run.planning_run_id,
            builder=RecordingBuilder(),
            narrative_axis_id=uuid4(),
        )

    unique, proposed4, *_rest4 = _ready(UniqueAxisUriPersistence())
    _orchestrator(unique, decider=RecordingDecider(_decision_proposal())).review(
        proposed4.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
    )
    run4 = unique.get_run(proposed4.planning_run.planning_run_id)
    unique.save_run(
        run4.model_copy(
            update={
                "artifacts": run4.artifacts.model_copy(update={"axis_feedback_decision_id": uuid4()})
            }
        )
    )
    with pytest.raises(ValueError, match="points at a different decision"):
        _approve(unique, proposed4.planning_run.planning_run_id, builder=RecordingBuilder())


def test_no_graph_understanding_or_search_rebuild() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    before = _snapshot(store, lineage, extractor, synth)
    understanding_id = proposed.editorial_understanding.understanding_id
    result, builder = _approve(store, proposed.planning_run.planning_run_id)
    assert len(builder.calls) == 1
    assert builder.calls[0]["understanding"].understanding_id == understanding_id
    assert builder.calls[0]["understanding"].central_ideas
    prompt = _prompt_text(EDITORIAL_PROJECT_PROMPT)
    assert "central_ideas" in prompt
    assert result.editorial_understanding.understanding_id == understanding_id
    assert store.understanding_writes == before["understanding_writes"]
    assert store.extraction_writes == before["extraction_writes"]
    assert store.graph_writes == before["graph_writes"]
    _assert_untouched(store, lineage, extractor, synth, before)
    lineage_after = store.get_source_lineage(lineage.source_lineage_id)
    assert lineage_after.state is SourceLineageState.GRAPH_READY
    assert lineage_after.graph_artifact_id == lineage.graph_artifact_id


def test_idempotent_retry_does_not_create_duplicate_project() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    first, builder = _approve(store, proposed.planning_run.planning_run_id)
    writes = store.project_writes
    second, builder2 = _approve(store, proposed.planning_run.planning_run_id, builder=RecordingBuilder())
    assert second.editorial_project.editorial_project_id == first.editorial_project.editorial_project_id
    assert second.planning_run.state is PlanningRunState.PROJECT_PROPOSED
    assert len(builder.calls) == 1
    assert builder2.calls == []
    assert store.project_writes == writes
    assert len(store.stored_projects) == 1


def test_practical_dimension_is_preserved() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    result, _builder = _approve(
        store,
        proposed.planning_run.planning_run_id,
        builder=RecordingBuilder(_proposal(practical="Como conversar sem esconder nem sobrecarregar.")),
    )
    assert result.editorial_project.core_subjects[0].practical_dimension == "Como conversar sem esconder nem sobrecarregar."
    assert result.editorial_project.llm_subjects
    assert result.editorial_project.enrichment_opportunities
    assert result.editorial_project.working_title
    assert result.editorial_project.editorial_promise


def test_human_gate_remains_and_no_article_is_produced() -> None:
    store, proposed, _lineage, _extractor, _request, _synth, _prop = _ready()
    result, _builder = _approve(store, proposed.planning_run.planning_run_id)
    assert result.human_approval_required is True
    assert result.stopped_at == "project_proposed"
    assert result.editorial_project.status.value == "proposed"
    assert result.narrative_axis.status is NarrativeAxisStatus.APPROVED
    assert result.article_created is False
    stored_project = store.get_project(result.editorial_project.editorial_project_id)
    assert stored_project.editorial_project.status.value == "proposed"
    persisted = store.load_editorial_project(result.planning_run.artifacts.editorial_project_uri)
    assert persisted.contract_version == EDITORIAL_PROJECT_CONTRACT_VERSION
    assert persisted.prompt_version == EDITORIAL_PROJECT_PROMPT_VERSION
    assert persisted.model == FAKE_PROJECT
    assert persisted.understanding_id == result.editorial_understanding.understanding_id
    assert persisted.editorial_project.status.value == "proposed"


def test_project_failure_preserves_understanding_and_does_not_approve_axis() -> None:
    store, proposed, lineage, extractor, _request, synth, _prop = _ready()
    before = _snapshot(store, lineage, extractor, synth)
    with pytest.raises(RuntimeError, match="project failed"):
        EditorialProjectOrchestrator(store, _project_runtime(), FailingBuilder()).execute(
            proposed.planning_run.planning_run_id
        )
    run = store.get_run(proposed.planning_run.planning_run_id)
    assert run.state is PlanningRunState.AXIS_PROPOSED
    assert run.artifacts.editorial_project_id is None
    stored_axis = store.get_axis(proposed.narrative_axis.narrative_axis_id)
    assert stored_axis.narrative_axis.status is NarrativeAxisStatus.PROPOSED
    assert stored_axis.narrative_axis.approved_at is None
    assert store.project_writes == 0
    _assert_untouched(store, lineage, extractor, synth, before)


def test_consumed_fields_must_be_paired() -> None:
    store, proposed, *_rest = _ready()
    decided = _orchestrator(store, decider=RecordingDecider(_decision_proposal())).review(
        proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
    )
    from comunicamundo_v4.hefesto.contracts import AxisFeedbackDecision

    payload = decided.decision.model_dump(mode="json")
    payload.pop("consumed_at", None)
    payload.pop("consumed_by_editorial_project_id", None)
    with pytest.raises(ValueError, match="consumed_at and consumed_by_editorial_project_id"):
        AxisFeedbackDecision.model_validate({**payload, "consumed_at": decided.decision.created_at.isoformat()})
    marked = AxisFeedbackDecision.model_validate(
        {
            **payload,
            "consumed_at": decided.decision.created_at.isoformat(),
            "consumed_by_editorial_project_id": str(uuid4()),
        }
    )
    assert marked.consumed_at is not None
    assert marked.consumed_by_editorial_project_id is not None


def test_propose_axis_cannot_advance_a_project_proposed_run() -> None:
    store, proposed, *_rest = _ready()
    _approve(store, proposed.planning_run.planning_run_id)
    from comunicamundo_v4.hefesto.axis_proposal import AxisProposalOrchestrator
    from test_axis_proposal import RecordingEvidenceReviewer, RecordingProposer, RecordingSynthesizer, _axis_runtime

    with pytest.raises(ValueError, match="already advanced past axis_proposed"):
        AxisProposalOrchestrator(
            store,
            _axis_runtime(),
            RecordingSynthesizer(),
            RecordingProposer(),
            RecordingEvidenceReviewer(),
        ).execute(proposed.planning_run.planning_run_id)


def test_gcp_persists_approved_axis_and_proposed_project() -> None:
    storage = FakeStorageClient()
    firestore = FakeFirestore()
    settings = GcpPersistenceSettings(project="test-project", bucket="test-bucket")
    persistence = GcpEditorialPersistence(
        artifacts=GcsArtifactStore(storage, bucket_name=settings.bucket, root_prefix=settings.root_prefix),
        repository=FirestoreEditorialRepository(
            firestore,
            axes_collection=settings.axes_collection,
            projects_collection=settings.projects_collection,
            articles_collection=settings.articles_collection,
            planning_runs_collection=settings.planning_runs_collection,
            planning_intents_collection=settings.planning_intents_collection,
            source_lineages_collection=settings.source_lineages_collection,
            source_lineage_keys_collection=settings.source_lineage_keys_collection,
        ),
    )
    run, _lineage, _extractor, _request = _prepare_graph_ready(
        persistence, "A criança percebe a ausência.", "O adulto acompanha."
    )
    proposed, _synth, _prop = _propose(persistence, run)
    result, _builder = _approve(persistence, proposed.planning_run.planning_run_id)
    names = list(storage.bucket("test-bucket").blobs)
    assert any(name.endswith("editorial-project.json") for name in names)
    loaded = persistence.load_editorial_project(result.planning_run.artifacts.editorial_project_uri)
    assert loaded.editorial_project.editorial_project_id == result.editorial_project.editorial_project_id
    stored_axis = persistence.get_axis(result.narrative_axis.narrative_axis_id)
    assert stored_axis.narrative_axis.status is NarrativeAxisStatus.APPROVED
    assert stored_axis.editorial_project_id == result.editorial_project.editorial_project_id
    stored_project = persistence.get_project(result.editorial_project.editorial_project_id)
    assert stored_project.editorial_project.status.value == "proposed"
    persisted_run = persistence.get_run(result.planning_run.planning_run_id)
    assert persisted_run.state is PlanningRunState.PROJECT_PROPOSED


def test_history_format_uses_current_main_contracts() -> None:
    store, proposed, *_rest = _ready()
    decided = _orchestrator(store, decider=RecordingDecider(_decision_proposal())).review(
        proposed.planning_run.planning_run_id, COMPLEMENT_FEEDBACK
    )
    rendered = format_feedback_history((decided.decision,))
    assert str(decided.decision.feedback_decision_id) in rendered
    assert "keep" in rendered
    assert "previous=" in rendered
    assert COMPLEMENT_FEEDBACK in rendered
    assert "axis_revised" not in rendered
    assert "carried_directive" not in rendered

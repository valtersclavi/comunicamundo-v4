"""Durable editorial understanding + Narrative Axis for a PlanningRun at graph_ready."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from hashlib import sha256
import json
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Protocol, Sequence
from uuid import UUID

from .contracts import (
    EDITORIAL_UNDERSTANDING_CONTRACT_VERSION,
    EDITORIAL_UNDERSTANDING_PROMPT_VERSION,
    NARRATIVE_AXIS_CONTRACT_VERSION,
    NARRATIVE_AXIS_PROMPT_VERSION,
    AxisProposalResult,
    EditorialBrief,
    EditorialUnderstanding,
    EditorialUnderstandingProposal,
    GraphReadyManifest,
    NarrativeAxis,
    NarrativeAxisProposal,
    NarrativeAxisStatus,
    PersistedNarrativeAxis,
    PlanningRequest,
    PlanningRun,
    PlanningRunState,
    PreparedChunk,
    SourceLineage,
    SourceLineageState,
)
from .graph import ProjectGraph
from .persistence import StoredNarrativeAxis
from .prompts import EDITORIAL_UNDERSTANDING_PROMPT, NARRATIVE_AXIS_FROM_UNDERSTANDING_PROMPT
from .runtime import AxisProposalRuntime

_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+")
_STATE_ORDER = list(PlanningRunState)


def _now() -> datetime:
    return datetime.now(UTC)


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def graph_ready_identity_hash(manifest: GraphReadyManifest) -> str:
    payload = {
        "source_lineage_id": str(manifest.source_lineage_id),
        "source_fingerprint": manifest.source_fingerprint,
        "extraction_manifest_uri": manifest.extraction_manifest_uri,
        "extraction_manifest_hash": manifest.extraction_manifest_hash,
        "extraction_contract_version": manifest.extraction_contract_version,
        "extraction_extractor_version": manifest.extraction_extractor_version,
        "extraction_model": manifest.extraction_model,
        "contract_version": manifest.contract_version,
        "indexer_version": manifest.indexer_version,
        "embedding_model": manifest.embedding_model,
        "embedding_package_version": manifest.embedding_package_version,
        "embedding_config": manifest.embedding_config,
        "graph_artifact_id": str(manifest.graph_artifact_id),
        "graph_artifact_version": manifest.graph_artifact_version,
    }
    return sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


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


def format_source_chunks(chunks: Sequence[PreparedChunk]) -> str:
    return "\n\n".join(
        f"[chunk {chunk.sequence} source={chunk.source_id} id={chunk.chunk_id}]\n{chunk.text}"
        for chunk in chunks
    )


def _join_lines(values: Sequence[str]) -> str:
    return "\n".join(values) if values else "(none)"


@dataclass(frozen=True, slots=True)
class UnderstandingMaterial:
    brief: EditorialBrief
    entities: tuple[str, ...]
    relations: tuple[str, ...]
    chunks: tuple[PreparedChunk, ...]


class UnderstandingSynthesizer(Protocol):
    def synthesize(self, material: UnderstandingMaterial) -> EditorialUnderstandingProposal: ...


class NarrativeAxisProposer(Protocol):
    def propose(self, *, brief: EditorialBrief, understanding: EditorialUnderstanding) -> NarrativeAxisProposal: ...


class AxisProposalPersistence(Protocol):
    def get_run(self, planning_run_id: UUID) -> PlanningRun | None: ...
    def save_run(self, run: PlanningRun) -> PlanningRun: ...
    def get_source_lineage(self, source_lineage_id: UUID) -> SourceLineage | None: ...
    def load_request(self, uri: str) -> PlanningRequest: ...
    def load_chunk_manifest(self, uri: str) -> object: ...
    def load_chunk(self, uri: str) -> PreparedChunk: ...
    def load_graph_ready_manifest(self, uri: str) -> GraphReadyManifest: ...
    def restore_source_lineage_graph(self, artifact, destination: Path) -> None: ...
    def persist_understanding(self, run: PlanningRun, understanding: EditorialUnderstanding) -> str: ...
    def load_understanding(self, uri: str) -> EditorialUnderstanding: ...
    def persist_narrative_axis(self, run: PlanningRun, record: PersistedNarrativeAxis) -> str: ...
    def load_narrative_axis(self, uri: str) -> PersistedNarrativeAxis: ...
    def save_axis(self, stored: StoredNarrativeAxis) -> StoredNarrativeAxis: ...


class LlamaIndexUnderstandingSynthesizer:
    def __init__(self, runtime: AxisProposalRuntime) -> None:
        if runtime.context_window <= 0 or runtime.understanding_max_tokens <= 0:
            raise ValueError("understanding runtime must declare explicit context and output budgets")
        self._runtime = runtime

    def synthesize(self, material: UnderstandingMaterial) -> EditorialUnderstandingProposal:
        if not material.entities and not material.relations:
            raise ValueError("editorial understanding requires a restored PropertyGraph")
        if not material.chunks:
            raise ValueError("editorial understanding requires persisted source chunks")
        return self._runtime.understanding_llm.structured_predict(
            EditorialUnderstandingProposal,
            EDITORIAL_UNDERSTANDING_PROMPT,
            theme=material.brief.theme,
            approach=material.brief.approach,
            audience=material.brief.audience or "not specified",
            product=material.brief.product,
            duration_minutes=material.brief.duration_minutes,
            output_language=material.brief.output_language,
            graph_entities=_join_lines(material.entities),
            graph_relations=_join_lines(material.relations),
            source_chunks=format_source_chunks(material.chunks),
        )


class LlamaIndexAxisProposer:
    def __init__(self, runtime: AxisProposalRuntime) -> None:
        if runtime.context_window <= 0 or runtime.axis_max_tokens <= 0:
            raise ValueError("Narrative Axis runtime must declare explicit context and output budgets")
        self._runtime = runtime

    def propose(self, *, brief: EditorialBrief, understanding: EditorialUnderstanding) -> NarrativeAxisProposal:
        return self._runtime.axis_llm.structured_predict(
            NarrativeAxisProposal,
            NARRATIVE_AXIS_FROM_UNDERSTANDING_PROMPT,
            theme=brief.theme,
            approach=brief.approach,
            audience=brief.audience or "not specified",
            product=brief.product,
            duration_minutes=brief.duration_minutes,
            output_language=brief.output_language,
            synthesis=understanding.synthesis,
            central_ideas=_join_lines(understanding.central_ideas),
            relations_and_tensions=_join_lines(understanding.relations_and_tensions),
            nuances=_join_lines(understanding.nuances),
            source_limits=_join_lines(understanding.source_limits),
            editorial_opportunities=_join_lines(understanding.editorial_opportunities),
        )


class AxisProposalOrchestrator:
    """Advance a PlanningRun from a graph_ready SourceLineage to axis_proposed."""

    terminal_state = PlanningRunState.AXIS_PROPOSED

    def __init__(
        self,
        persistence: AxisProposalPersistence,
        runtime: AxisProposalRuntime,
        synthesizer: UnderstandingSynthesizer,
        proposer: NarrativeAxisProposer,
        *,
        understanding_contract_version: str = EDITORIAL_UNDERSTANDING_CONTRACT_VERSION,
        understanding_prompt_version: str = EDITORIAL_UNDERSTANDING_PROMPT_VERSION,
        axis_contract_version: str = NARRATIVE_AXIS_CONTRACT_VERSION,
        axis_prompt_version: str = NARRATIVE_AXIS_PROMPT_VERSION,
    ) -> None:
        self._persistence = persistence
        self._runtime = runtime
        self._synthesizer = synthesizer
        self._proposer = proposer
        self._understanding_contract = understanding_contract_version
        self._understanding_prompt = understanding_prompt_version
        self._axis_contract = axis_contract_version
        self._axis_prompt = axis_prompt_version

    def execute(self, planning_run_id: UUID) -> AxisProposalResult:
        run, lineage, request, graph_manifest, identity_hash = self._load_validated(planning_run_id)
        reusable = self._reusable_axis(run, lineage, identity_hash)
        if reusable is not None:
            understanding, axis = reusable
            return self._result(run, understanding, axis)
        run = self._mark_graph_ready(run, lineage)
        try:
            understanding = self._ensure_understanding(run, lineage, request, graph_manifest, identity_hash)
        except Exception as exc:
            self._persistence.save_run(
                run.model_copy(update={"error": str(exc), "updated_at": _now()})
            )
            raise
        run = self._persistence.get_run(run.planning_run_id) or run
        try:
            axis = self._ensure_axis(run, lineage, request, understanding, identity_hash, graph_manifest)
        except Exception as exc:
            self._persistence.save_run(
                run.model_copy(
                    update={
                        "state": PlanningRunState.UNDERSTANDING_READY,
                        "error": str(exc),
                        "updated_at": _now(),
                    }
                )
            )
            raise
        run = self._persistence.get_run(run.planning_run_id) or run
        return self._result(run, understanding, axis)

    def _load_validated(
        self, planning_run_id: UUID
    ) -> tuple[PlanningRun, SourceLineage, PlanningRequest, GraphReadyManifest, str]:
        run = self._persistence.get_run(planning_run_id)
        if run is None:
            raise KeyError(f"PlanningRun not found: {planning_run_id}")
        if run.artifacts.request_uri is None:
            raise ValueError("PlanningRun does not have a persisted request")
        lineage = self._persistence.get_source_lineage(run.source_lineage_id)
        if lineage is None:
            raise KeyError(f"SourceLineage not found: {run.source_lineage_id}")
        if lineage.state is not SourceLineageState.GRAPH_READY:
            raise ValueError("SourceLineage is not graph_ready")
        if lineage.graph_manifest_uri is None or lineage.graph_artifact_id is None:
            raise ValueError("SourceLineage is missing a persisted PropertyGraph")
        if lineage.chunk_manifest_uri is None:
            raise ValueError("SourceLineage is missing persisted source chunks")
        request = self._persistence.load_request(run.artifacts.request_uri)
        if request.project_id != run.project_id:
            raise ValueError("persisted planning request does not belong to this PlanningRun")
        graph_manifest = self._persistence.load_graph_ready_manifest(lineage.graph_manifest_uri)
        if graph_manifest.source_lineage_id != lineage.source_lineage_id:
            raise ValueError("graph_ready manifesto does not belong to this SourceLineage")
        if graph_manifest.source_fingerprint != lineage.source_fingerprint:
            raise ValueError("graph_ready manifesto source fingerprint mismatch")
        if graph_manifest.graph_artifact_id != lineage.graph_artifact_id:
            raise ValueError("graph_ready manifesto artifact does not match the SourceLineage")
        if graph_manifest.source_fingerprint != run.source_fingerprint:
            raise ValueError("PlanningRun source fingerprint does not match the graph_ready SourceLineage")
        identity_hash = graph_ready_identity_hash(graph_manifest)
        return run, lineage, request, graph_manifest, identity_hash

    def _mark_graph_ready(self, run: PlanningRun, lineage: SourceLineage) -> PlanningRun:
        artifacts = run.artifacts.model_copy(
            update={
                "chunk_manifest_uri": lineage.chunk_manifest_uri,
                "graph_extractions_uri": lineage.graph_extractions_manifest_uri,
                "graph_artifact_id": lineage.graph_artifact_id,
            }
        )
        if _before(run.state, PlanningRunState.GRAPH_READY) or run.artifacts != artifacts or run.error:
            run = self._persistence.save_run(
                run.model_copy(
                    update={
                        "state": run.state if not _before(run.state, PlanningRunState.GRAPH_READY) else PlanningRunState.GRAPH_READY,
                        "artifacts": artifacts,
                        "chunk_count": lineage.chunk_count,
                        "error": None,
                        "updated_at": _now(),
                    }
                )
            )
        return run

    def _ensure_understanding(
        self,
        run: PlanningRun,
        lineage: SourceLineage,
        request: PlanningRequest,
        graph_manifest: GraphReadyManifest,
        identity_hash: str,
    ) -> EditorialUnderstanding:
        existing = self._load_compatible_understanding(run, lineage, identity_hash)
        if existing is not None:
            if run.state is not PlanningRunState.UNDERSTANDING_READY and _before(run.state, PlanningRunState.AXIS_PROPOSED):
                self._persistence.save_run(
                    run.model_copy(
                        update={
                            "state": PlanningRunState.UNDERSTANDING_READY,
                            "artifacts": run.artifacts.model_copy(update={"understanding_uri": run.artifacts.understanding_uri}),
                            "error": None,
                            "updated_at": _now(),
                        }
                    )
                )
            return existing
        material = self._load_material(lineage, request, graph_manifest)
        proposal = self._synthesizer.synthesize(material)
        understanding = EditorialUnderstanding(
            planning_run_id=run.planning_run_id,
            project_id=run.project_id,
            source_lineage_id=lineage.source_lineage_id,
            contract_version=self._understanding_contract,
            prompt_version=self._understanding_prompt,
            model=self._runtime.understanding_model,
            request_fingerprint=run.request_fingerprint,
            brief_fingerprint=run.brief_fingerprint,
            source_fingerprint=run.source_fingerprint,
            graph_artifact_id=lineage.graph_artifact_id,
            graph_manifest_uri=lineage.graph_manifest_uri,
            graph_manifest_hash=identity_hash,
            chunk_manifest_uri=lineage.chunk_manifest_uri,
            chunk_ids=[chunk.chunk_id for chunk in material.chunks],
            graph_entity_count=len(material.entities),
            graph_relation_count=len(material.relations),
            central_ideas=proposal.central_ideas,
            relations_and_tensions=proposal.relations_and_tensions,
            nuances=proposal.nuances,
            source_limits=proposal.source_limits,
            editorial_opportunities=proposal.editorial_opportunities,
            synthesis=proposal.synthesis,
        )
        uri = self._persistence.persist_understanding(run, understanding)
        self._persistence.save_run(
            run.model_copy(
                update={
                    "state": PlanningRunState.UNDERSTANDING_READY,
                    "artifacts": run.artifacts.model_copy(update={"understanding_uri": uri}),
                    "error": None,
                    "updated_at": _now(),
                }
            )
        )
        return understanding

    def _ensure_axis(
        self,
        run: PlanningRun,
        lineage: SourceLineage,
        request: PlanningRequest,
        understanding: EditorialUnderstanding,
        identity_hash: str,
        graph_manifest: GraphReadyManifest,
    ) -> NarrativeAxis:
        existing = self._load_compatible_axis(run, lineage, understanding, identity_hash)
        if existing is not None:
            if run.state is not PlanningRunState.AXIS_PROPOSED:
                self._persistence.save_run(
                    run.model_copy(
                        update={
                            "state": PlanningRunState.AXIS_PROPOSED,
                            "error": None,
                            "updated_at": _now(),
                        }
                    )
                )
            return existing
        proposal = self._proposer.propose(brief=request.brief, understanding=understanding)
        statement = validate_axis_statement(proposal.statement)
        axis = NarrativeAxis(
            project_id=run.project_id,
            statement=statement,
            status=NarrativeAxisStatus.PROPOSED,
        )
        record = PersistedNarrativeAxis(
            contract_version=self._axis_contract,
            prompt_version=self._axis_prompt,
            model=self._runtime.axis_model,
            planning_run_id=run.planning_run_id,
            project_id=run.project_id,
            source_lineage_id=lineage.source_lineage_id,
            understanding_id=understanding.understanding_id,
            request_fingerprint=run.request_fingerprint,
            brief_fingerprint=run.brief_fingerprint,
            source_fingerprint=run.source_fingerprint,
            graph_artifact_id=lineage.graph_artifact_id,
            graph_manifest_hash=identity_hash,
            narrative_axis=axis,
        )
        uri = self._persistence.persist_narrative_axis(run, record)
        self._persistence.save_axis(
            StoredNarrativeAxis(
                narrative_axis=axis,
                brief=request.brief,
                graph_artifact=graph_manifest.graph_artifact,
                graph_context=understanding.synthesis,
                updated_at=_now(),
            )
        )
        self._persistence.save_run(
            run.model_copy(
                update={
                    "state": PlanningRunState.AXIS_PROPOSED,
                    "artifacts": run.artifacts.model_copy(
                        update={"narrative_axis_id": axis.narrative_axis_id, "narrative_axis_uri": uri}
                    ),
                    "error": None,
                    "updated_at": _now(),
                }
            )
        )
        return axis

    def _load_material(
        self, lineage: SourceLineage, request: PlanningRequest, graph_manifest: GraphReadyManifest
    ) -> UnderstandingMaterial:
        chunks = self._load_chunks(lineage)
        with TemporaryDirectory(prefix="hefesto-v4-axis-graph-") as temporary:
            destination = Path(temporary) / "graph"
            self._persistence.restore_source_lineage_graph(graph_manifest.graph_artifact, destination)
            graph = ProjectGraph.load(destination, embed_model=self._runtime.embed_model)
            entities = tuple(sorted({node.name for node in graph.entity_nodes()}))
            relations = tuple(sorted(f"{item.source_id} {item.label} {item.target_id}" for item in graph.relations()))
        if not entities and not relations:
            raise ValueError("restored PropertyGraph has no entities or relations")
        if not chunks:
            raise ValueError("persisted source chunks are empty")
        return UnderstandingMaterial(brief=request.brief, entities=entities, relations=relations, chunks=chunks)

    def _load_chunks(self, lineage: SourceLineage) -> tuple[PreparedChunk, ...]:
        manifest = self._persistence.load_chunk_manifest(lineage.chunk_manifest_uri)
        loaded = [self._persistence.load_chunk(entry.chunk_uri) for entry in manifest.chunks]
        return tuple(sorted(loaded, key=lambda chunk: chunk.sequence))

    def _load_compatible_understanding(
        self, run: PlanningRun, lineage: SourceLineage, identity_hash: str
    ) -> EditorialUnderstanding | None:
        if run.artifacts.understanding_uri is None:
            return None
        try:
            stored = self._persistence.load_understanding(run.artifacts.understanding_uri)
        except Exception:
            return None
        if not self._understanding_compatible(stored, run, lineage, identity_hash):
            return None
        return stored

    def _understanding_compatible(
        self,
        stored: EditorialUnderstanding,
        run: PlanningRun,
        lineage: SourceLineage,
        identity_hash: str,
    ) -> bool:
        return (
            stored.planning_run_id == run.planning_run_id
            and stored.project_id == run.project_id
            and stored.source_lineage_id == lineage.source_lineage_id
            and stored.request_fingerprint == run.request_fingerprint
            and stored.brief_fingerprint == run.brief_fingerprint
            and stored.source_fingerprint == run.source_fingerprint
            and stored.graph_artifact_id == lineage.graph_artifact_id
            and stored.graph_manifest_uri == lineage.graph_manifest_uri
            and stored.graph_manifest_hash == identity_hash
            and stored.chunk_manifest_uri == lineage.chunk_manifest_uri
            and stored.contract_version == self._understanding_contract
            and stored.prompt_version == self._understanding_prompt
            and stored.model == self._runtime.understanding_model
            and stored.synthesis.strip() != ""
            and stored.chunk_ids
        )

    def _load_compatible_axis(
        self,
        run: PlanningRun,
        lineage: SourceLineage,
        understanding: EditorialUnderstanding,
        identity_hash: str,
    ) -> NarrativeAxis | None:
        if run.artifacts.narrative_axis_uri is None:
            return None
        try:
            stored = self._persistence.load_narrative_axis(run.artifacts.narrative_axis_uri)
        except Exception:
            return None
        if not self._axis_compatible(stored, run, lineage, understanding, identity_hash):
            return None
        try:
            validate_axis_statement(stored.narrative_axis.statement)
        except Exception:
            return None
        if stored.narrative_axis.status is not NarrativeAxisStatus.PROPOSED:
            return None
        if stored.narrative_axis.approved_at is not None:
            return None
        return stored.narrative_axis

    def _axis_compatible(
        self,
        stored: PersistedNarrativeAxis,
        run: PlanningRun,
        lineage: SourceLineage,
        understanding: EditorialUnderstanding,
        identity_hash: str,
    ) -> bool:
        return (
            stored.planning_run_id == run.planning_run_id
            and stored.project_id == run.project_id
            and stored.source_lineage_id == lineage.source_lineage_id
            and stored.understanding_id == understanding.understanding_id
            and stored.request_fingerprint == run.request_fingerprint
            and stored.brief_fingerprint == run.brief_fingerprint
            and stored.source_fingerprint == run.source_fingerprint
            and stored.graph_artifact_id == lineage.graph_artifact_id
            and stored.graph_manifest_hash == identity_hash
            and stored.contract_version == self._axis_contract
            and stored.prompt_version == self._axis_prompt
            and stored.model == self._runtime.axis_model
            and stored.narrative_axis.project_id == run.project_id
        )

    def _reusable_axis(
        self, run: PlanningRun, lineage: SourceLineage, identity_hash: str
    ) -> tuple[EditorialUnderstanding, NarrativeAxis] | None:
        if run.state is not PlanningRunState.AXIS_PROPOSED:
            return None
        understanding = self._load_compatible_understanding(run, lineage, identity_hash)
        if understanding is None:
            return None
        axis = self._load_compatible_axis(run, lineage, understanding, identity_hash)
        if axis is None:
            return None
        return understanding, axis

    def _result(self, run: PlanningRun, understanding: EditorialUnderstanding, axis: NarrativeAxis) -> AxisProposalResult:
        return AxisProposalResult(
            planning_run=run,
            editorial_understanding=understanding,
            narrative_axis=axis,
            proposed_axis=axis.statement,
            human_approval_required=True,
            editorial_project_created=False,
            stopped_at="axis_proposed",
        )


def _before(state: PlanningRunState, target: PlanningRunState) -> bool:
    return _STATE_ORDER.index(state) < _STATE_ORDER.index(target)

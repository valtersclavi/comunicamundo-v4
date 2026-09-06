"""Bind V2 digestion prompts and contracts to the central asynchronous gateway."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from typing import Generic, Protocol, TypeVar

from pydantic import BaseModel

from .contracts import EditorialBrief, NormalizedSource
from .digestion_contracts import (
    DIGESTION_ENGINE_VERSION,
    EVIDENCE_MATRIX_PROMPT_VERSION,
    SECTION_DIGEST_PROMPT_VERSION,
    SOURCE_DOSSIER_PROMPT_VERSION,
    CausalStatus,
    CategorizedText,
    DigestionPlan,
    EvidenceMatrix,
    EvidenceMatrixProposal,
    EvidenceStrength,
    DigestionItemKind,
    DigestionItemProposal,
    MethodProfile,
    ReducedSourceDossierProposal,
    SectionDigest,
    SectionDigestProposal,
    SourceDigestionPlan,
    SourceDossier,
    SourceDossierProposal,
    SourceFinding,
    SourceFindingProposal,
    EvidenceKeyReference,
    canonical_sha256,
)
from .digestion_prompts import (
    DIGESTION_SYSTEM_INSTRUCTION,
    direct_source_dossier_prompt,
    evidence_matrix_prompt,
    reduced_source_dossier_prompt,
    section_digest_prompt,
)
from .execution_contracts import LLMCallIntent, TokenBudget, text_sha256, work_unit_key
from .planning import fingerprint_brief
from .llm_gateway import bind_json_output_contract
from .source_digestion import (
    estimate_tokens,
    materialize_findings,
    source_balance_metrics,
    validate_dossier_evidence,
    validate_reduced_dossier_evidence,
)


OutputT = TypeVar("OutputT")
ProposalT = TypeVar("ProposalT", bound=BaseModel)


class GatewayResult(Protocol):
    parsed: BaseModel | None


class ModelGateway(Protocol):
    async def generate(
        self,
        intent: LLMCallIntent,
        *,
        prompt: str,
        system_instruction: str | None = None,
        response_schema: type[BaseModel] | None = None,
    ) -> GatewayResult: ...


@dataclass(frozen=True, slots=True)
class DigestionRuntimeConfig:
    model: str = "gemini-2.5-flash"
    location: str = "global"
    temperature: float = 0.1
    context_window_tokens: int = 1_048_576
    max_input_tokens: int = 250_000
    section_max_output_tokens: int = 65_536
    section_capacity_output_tokens: int = 12_000
    section_thinking_tokens: int = 1_000
    dossier_max_output_tokens: int = 65_536
    dossier_capacity_output_tokens: int = 16_000
    dossier_thinking_tokens: int = 2_000
    matrix_max_output_tokens: int = 65_536
    matrix_capacity_output_tokens: int = 16_000
    matrix_thinking_tokens: int = 3_000
    max_attempts: int = 3

    def __post_init__(self) -> None:
        if not self.model.strip() or not self.location.strip():
            raise ValueError("digestion runtime requires a model and location")
        if not 0 <= self.temperature <= 2:
            raise ValueError("temperature must fit the provider contract")
        if self.max_input_tokens <= 0 or self.context_window_tokens <= 0:
            raise ValueError("input and context budgets must be positive")
        for output, capacity, thinking in (
            (self.section_max_output_tokens, self.section_capacity_output_tokens, self.section_thinking_tokens),
            (self.dossier_max_output_tokens, self.dossier_capacity_output_tokens, self.dossier_thinking_tokens),
            (self.matrix_max_output_tokens, self.matrix_capacity_output_tokens, self.matrix_thinking_tokens),
        ):
            if output <= 0 or capacity <= 0 or capacity > output or thinking < 0 or thinking > output:
                raise ValueError("each task requires a coherent output and thinking budget")
            if self.max_input_tokens + output > self.context_window_tokens:
                raise ValueError("task budget exceeds the model context window")
        if not 1 <= self.max_attempts <= 8:
            raise ValueError("attempt budget must be between one and eight")

    @property
    def config_sha256(self) -> str:
        return canonical_sha256(asdict(self))


@dataclass(frozen=True, slots=True)
class PreparedDigestionCall(Generic[OutputT, ProposalT]):
    intent: LLMCallIntent
    prompt: str
    response_schema: type[ProposalT]
    output_schema: type[OutputT]
    input_sha256s: tuple[str, ...]
    finalize: Callable[[ProposalT], OutputT]

    @property
    def work_unit_id(self) -> str:
        return self.intent.work_unit_id

    @property
    def reserved_capacity_tokens(self) -> int:
        return self.intent.budget.reserved_capacity_tokens

    async def invoke(self, gateway: ModelGateway) -> OutputT:
        result = await gateway.generate(
            self.intent,
            prompt=self.prompt,
            system_instruction=DIGESTION_SYSTEM_INSTRUCTION,
            response_schema=self.response_schema,
        )
        if not isinstance(result.parsed, self.response_schema):
            raise TypeError(f"gateway did not return {self.response_schema.__name__}")
        return self.finalize(result.parsed)


class DigestionCallFactory:
    """Constructs immutable, budgeted calls; it never calls the provider itself."""

    def __init__(self, config: DigestionRuntimeConfig) -> None:
        self.config = config

    def section_digest(
        self,
        *,
        plan: DigestionPlan,
        source: NormalizedSource,
        source_plan: SourceDigestionPlan,
        section_ordinal: int,
    ) -> PreparedDigestionCall[SectionDigest, SectionDigestProposal]:
        section = source_plan.sections[section_ordinal]
        prompt = section_digest_prompt(
            source=source,
            section=section,
            output_language=source.language,
        )
        prompt = bind_json_output_contract(prompt, SectionDigestProposal)
        intent = self._intent(
            plan=plan,
            task_type="section_digest",
            prompt_version=SECTION_DIGEST_PROMPT_VERSION,
            prompt=prompt,
            response_schema=SectionDigestProposal,
            input_sha256s=[section.section_id, section.content_sha256],
            max_output_tokens=self.config.section_max_output_tokens,
            capacity_output_tokens=self.config.section_capacity_output_tokens,
            thinking_tokens=self.config.section_thinking_tokens,
        )

        def finalize(proposal: SectionDigestProposal) -> SectionDigest:
            findings, grouped, other_items = _partition_items(proposal.items, section=True)
            return SectionDigest(
                synopsis=proposal.synopsis,
                findings=materialize_findings(findings, [section]),
                methods=grouped[DigestionItemKind.METHOD],
                limitations=grouped[DigestionItemKind.LIMITATION],
                uncertainties=grouped[DigestionItemKind.UNCERTAINTY],
                distinctive_contributions=grouped[DigestionItemKind.DISTINCTIVE_CONTRIBUTION],
                recommendations=grouped[DigestionItemKind.RECOMMENDATION],
                methodological_facts=grouped[DigestionItemKind.METHODOLOGICAL_FACT],
                other_items=other_items,
                source_id=source.source_id,
                section_id=section.section_id,
                section_sha256=section.content_sha256,
                model=self.config.model,
            )

        return PreparedDigestionCall(
            intent=intent,
            prompt=prompt,
            response_schema=SectionDigestProposal,
            output_schema=SectionDigest,
            input_sha256s=(section.section_id, section.content_sha256),
            finalize=finalize,
        )

    def direct_dossier(
        self,
        *,
        plan: DigestionPlan,
        source: NormalizedSource,
        source_plan: SourceDigestionPlan,
        brief: EditorialBrief,
    ) -> PreparedDigestionCall[SourceDossier, SourceDossierProposal]:
        if source_plan.mode.value != "direct":
            raise ValueError("direct dossier call requires a direct source plan")
        section = source_plan.sections[0]
        prompt = direct_source_dossier_prompt(source=source, section=section, brief=brief)
        prompt = bind_json_output_contract(prompt, SourceDossierProposal)
        intent = self._intent(
            plan=plan,
            task_type="source_dossier_direct",
            prompt_version=SOURCE_DOSSIER_PROMPT_VERSION,
            prompt=prompt,
            response_schema=SourceDossierProposal,
            input_sha256s=[
                text_sha256(str(source_plan.source_id)),
                source_plan.source_sha256,
                fingerprint_brief(brief),
            ],
            max_output_tokens=self.config.dossier_max_output_tokens,
            capacity_output_tokens=self.config.dossier_capacity_output_tokens,
            thinking_tokens=self.config.dossier_thinking_tokens,
        )

        def finalize(proposal: SourceDossierProposal) -> SourceDossier:
            findings, grouped, other_items = _partition_items(proposal.items, section=False, allow_findings=True)
            dossier = self._dossier(
                plan,
                source_plan,
                proposal,
                findings=materialize_findings(findings, source_plan.sections),
                grouped=grouped,
                other_items=other_items,
            )
            validate_dossier_evidence(dossier, source_plan.sections)
            return dossier

        return PreparedDigestionCall(
            intent=intent,
            prompt=prompt,
            response_schema=SourceDossierProposal,
            output_schema=SourceDossier,
            input_sha256s=(
                text_sha256(str(source_plan.source_id)),
                source_plan.source_sha256,
                fingerprint_brief(brief),
            ),
            finalize=finalize,
        )

    def reduced_dossier(
        self,
        *,
        plan: DigestionPlan,
        source: NormalizedSource,
        source_plan: SourceDigestionPlan,
        section_digests: Sequence[SectionDigest],
        brief: EditorialBrief,
    ) -> PreparedDigestionCall[SourceDossier, ReducedSourceDossierProposal]:
        if source_plan.mode.value != "hierarchical":
            raise ValueError("dossier reduction requires a hierarchical source plan")
        expected = {section.section_id for section in source_plan.sections}
        actual = {digest.section_id for digest in section_digests}
        if expected != actual or len(section_digests) != len(source_plan.sections):
            raise ValueError("dossier reduction requires one digest for every source section")
        ordered = sorted(section_digests, key=lambda item: next(
            section.ordinal for section in source_plan.sections if section.section_id == item.section_id
        ))
        prompt = reduced_source_dossier_prompt(
            source=source,
            section_digests=ordered,
            brief=brief,
        )
        prompt = bind_json_output_contract(prompt, ReducedSourceDossierProposal)
        intent = self._intent(
            plan=plan,
            task_type="source_dossier_reduce",
            prompt_version=SOURCE_DOSSIER_PROMPT_VERSION,
            prompt=prompt,
            response_schema=ReducedSourceDossierProposal,
            input_sha256s=[
                text_sha256(str(source_plan.source_id)),
                source_plan.source_sha256,
                *[digest.content_sha256 for digest in ordered],
                fingerprint_brief(brief),
            ],
            max_output_tokens=self.config.dossier_max_output_tokens,
            capacity_output_tokens=self.config.dossier_capacity_output_tokens,
            thinking_tokens=self.config.dossier_thinking_tokens,
        )

        def finalize(proposal: ReducedSourceDossierProposal) -> SourceDossier:
            _, grouped, other_items = _partition_items(proposal.items, section=False, allow_findings=False)
            dossier = self._dossier(
                plan,
                source_plan,
                proposal,
                findings=_carry_validated_findings(ordered),
                grouped=grouped,
                other_items=other_items,
            )
            validate_reduced_dossier_evidence(dossier, ordered, source_plan.sections)
            return dossier

        return PreparedDigestionCall(
            intent=intent,
            prompt=prompt,
            response_schema=ReducedSourceDossierProposal,
            output_schema=SourceDossier,
            input_sha256s=tuple(
                [
                    text_sha256(str(source_plan.source_id)),
                    source_plan.source_sha256,
                    *[digest.content_sha256 for digest in ordered],
                    fingerprint_brief(brief),
                ]
            ),
            finalize=finalize,
        )

    def evidence_matrix(
        self,
        *,
        plan: DigestionPlan,
        dossiers: Sequence[SourceDossier],
        brief: EditorialBrief,
    ) -> PreparedDigestionCall[EvidenceMatrix, EvidenceMatrixProposal]:
        expected = [item.source_id for item in plan.sources]
        if [item.source_id for item in dossiers] != expected:
            raise ValueError("matrix dossiers must match the planned source order exactly")
        prompt = evidence_matrix_prompt(dossiers=dossiers, brief=brief)
        prompt = bind_json_output_contract(prompt, EvidenceMatrixProposal)
        intent = self._intent(
            plan=plan,
            task_type="evidence_matrix",
            prompt_version=EVIDENCE_MATRIX_PROMPT_VERSION,
            prompt=prompt,
            response_schema=EvidenceMatrixProposal,
            input_sha256s=[*[item.content_sha256 for item in dossiers], fingerprint_brief(brief)],
            max_output_tokens=self.config.matrix_max_output_tokens,
            capacity_output_tokens=self.config.matrix_capacity_output_tokens,
            thinking_tokens=self.config.matrix_thinking_tokens,
        )

        def finalize(proposal: EvidenceMatrixProposal) -> EvidenceMatrix:
            return EvidenceMatrix(
                **proposal.model_dump(),
                execution_run_id=plan.execution_run_id,
                project_id=plan.project_id,
                source_fingerprint=plan.source_fingerprint,
                source_ids=expected,
                dossier_sha256s=[item.content_sha256 for item in dossiers],
                source_balance=source_balance_metrics(dossiers),
                model=self.config.model,
            )

        return PreparedDigestionCall(
            intent=intent,
            prompt=prompt,
            response_schema=EvidenceMatrixProposal,
            output_schema=EvidenceMatrix,
            input_sha256s=tuple([*[item.content_sha256 for item in dossiers], fingerprint_brief(brief)]),
            finalize=finalize,
        )

    def _dossier(
        self,
        plan: DigestionPlan,
        source_plan: SourceDigestionPlan,
        proposal: SourceDossierProposal | ReducedSourceDossierProposal,
        *,
        findings: list[SourceFinding],
        grouped: dict[DigestionItemKind, list[str]],
        other_items: list[CategorizedText],
    ) -> SourceDossier:
        return SourceDossier(
            executive_synthesis=proposal.executive_synthesis,
            method_profile=MethodProfile(
                source_kind=_profile_value(grouped, DigestionItemKind.SOURCE_KIND),
                design=_profile_value(grouped, DigestionItemKind.DESIGN),
                population_or_scope=_profile_value(grouped, DigestionItemKind.POPULATION_OR_SCOPE),
                measures_or_material=_profile_value(grouped, DigestionItemKind.MEASURES_OR_MATERIAL),
                temporal_scope=_profile_value(grouped, DigestionItemKind.TEMPORAL_SCOPE),
            ),
            findings=findings,
            limitations=grouped[DigestionItemKind.LIMITATION],
            uncertainties_and_disputes=grouped[DigestionItemKind.UNCERTAINTY],
            boundary_conditions=grouped[DigestionItemKind.BOUNDARY_CONDITION],
            practical_implications=grouped[DigestionItemKind.PRACTICAL_IMPLICATION],
            distinctive_contributions=grouped[DigestionItemKind.DISTINCTIVE_CONTRIBUTION],
            coverage_gaps=grouped[DigestionItemKind.COVERAGE_GAP],
            recommendations=grouped[DigestionItemKind.RECOMMENDATION],
            methodological_facts=grouped[DigestionItemKind.METHODOLOGICAL_FACT],
            other_items=other_items,
            execution_run_id=plan.execution_run_id,
            project_id=plan.project_id,
            source_id=source_plan.source_id,
            source_ordinal=source_plan.source_ordinal,
            source_title=source_plan.title,
            source_language=source_plan.language,
            source_sha256=source_plan.source_sha256,
            source_char_count=source_plan.source_char_count,
            estimated_source_tokens=source_plan.estimated_source_tokens,
            digestion_mode=source_plan.mode,
            section_ids=[section.section_id for section in source_plan.sections],
            model=self.config.model,
        )
    def _intent(
        self,
        *,
        plan: DigestionPlan,
        task_type: str,
        prompt_version: str,
        prompt: str,
        response_schema: type[BaseModel],
        input_sha256s: list[str],
        max_output_tokens: int,
        capacity_output_tokens: int,
        thinking_tokens: int,
    ) -> LLMCallIntent:
        estimated_input = estimate_tokens(prompt) + estimate_tokens(DIGESTION_SYSTEM_INSTRUCTION)
        budget = TokenBudget(
            estimated_input_tokens=estimated_input,
            max_input_tokens=self.config.max_input_tokens,
            max_output_tokens=max_output_tokens,
            capacity_output_tokens=capacity_output_tokens,
            thinking_budget_tokens=thinking_tokens,
            context_window_tokens=self.config.context_window_tokens,
        )
        unit_id = work_unit_key(
            engine_version=DIGESTION_ENGINE_VERSION,
            unit_type=task_type,
            input_sha256s=input_sha256s,
            prompt_version=prompt_version,
            model=self.config.model,
            model_config_sha256=self.config.config_sha256,
            cache_scope=f"run:{plan.execution_run_id}",
        )
        return LLMCallIntent(
            execution_run_id=plan.execution_run_id,
            project_id=plan.project_id,
            work_unit_id=unit_id,
            task_type=task_type,
            prompt_version=prompt_version,
            prompt_sha256=text_sha256(prompt),
            system_instruction_sha256=text_sha256(DIGESTION_SYSTEM_INSTRUCTION),
            response_schema_name=f"{response_schema.__module__}.{response_schema.__qualname__}",
            model=self.config.model,
            location=self.config.location,
            temperature=self.config.temperature,
            budget=budget,
            max_attempts=self.config.max_attempts,
        )


def _partition_items(
    items: Sequence[DigestionItemProposal],
    *,
    section: bool,
    allow_findings: bool = True,
) -> tuple[list[SourceFindingProposal], dict[DigestionItemKind, list[str]], list[CategorizedText]]:
    """Route a flat transport into domain fields and group repeated anchors."""

    allowed = (
        {
            DigestionItemKind.FINDING,
            DigestionItemKind.METHOD,
            DigestionItemKind.LIMITATION,
            DigestionItemKind.UNCERTAINTY,
            DigestionItemKind.DISTINCTIVE_CONTRIBUTION,
            DigestionItemKind.RECOMMENDATION,
            DigestionItemKind.METHODOLOGICAL_FACT,
            DigestionItemKind.OTHER,
        }
        if section
        else {
            DigestionItemKind.FINDING,
            DigestionItemKind.LIMITATION,
            DigestionItemKind.UNCERTAINTY,
            DigestionItemKind.BOUNDARY_CONDITION,
            DigestionItemKind.PRACTICAL_IMPLICATION,
            DigestionItemKind.DISTINCTIVE_CONTRIBUTION,
            DigestionItemKind.COVERAGE_GAP,
            DigestionItemKind.RECOMMENDATION,
            DigestionItemKind.METHODOLOGICAL_FACT,
            DigestionItemKind.OTHER,
            DigestionItemKind.SOURCE_KIND,
            DigestionItemKind.DESIGN,
            DigestionItemKind.POPULATION_OR_SCOPE,
            DigestionItemKind.MEASURES_OR_MATERIAL,
            DigestionItemKind.TEMPORAL_SCOPE,
        }
    )
    grouped = {kind: [] for kind in DigestionItemKind}
    other_items: list[CategorizedText] = []
    finding_groups: dict[tuple[object, ...], SourceFindingProposal] = {}
    for item in items:
        evidence_values = (
            item.causal_status,
            item.evidence_strength,
            item.first_evidence_key,
            item.last_evidence_key,
        )
        if not all(evidence_values):
            try:
                kind = DigestionItemKind(item.kind)
            except ValueError:
                kind = None
            if kind is None or kind not in allowed or kind is DigestionItemKind.FINDING:
                other_items.append(CategorizedText(category=item.kind, text=item.text))
            else:
                grouped[kind].append(item.text)
            continue
        if not allow_findings:
            other_items.append(CategorizedText(category=item.kind, text=item.text))
            continue
        if (
            not item.causal_status
            or not item.evidence_strength
            or not item.first_evidence_key
            or not item.last_evidence_key
        ):
            raise ValueError("every finding item requires status, strength, and two evidence keys")
        try:
            causal_status = CausalStatus(item.causal_status)
        except ValueError:
            causal_status = CausalStatus.UNCLEAR
        try:
            evidence_strength = EvidenceStrength(item.evidence_strength)
        except ValueError:
            evidence_strength = EvidenceStrength.INDETERMINATE
        identity = (
            item.kind,
            item.text,
            causal_status,
            evidence_strength,
            tuple(item.qualifications),
        )
        anchor = EvidenceKeyReference(
            first_evidence_key=item.first_evidence_key,
            last_evidence_key=item.last_evidence_key,
        )
        existing = finding_groups.get(identity)
        if existing is None:
            finding_groups[identity] = SourceFindingProposal(
                category=item.kind,
                finding=item.text,
                causal_status=causal_status,
                evidence_strength=evidence_strength,
                anchors=[anchor],
                qualifications=item.qualifications,
            )
        elif anchor not in existing.anchors:
            finding_groups[identity] = existing.model_copy(update={"anchors": [*existing.anchors, anchor]})
    return list(finding_groups.values()), grouped, other_items


def _profile_value(
    grouped: dict[DigestionItemKind, list[str]],
    kind: DigestionItemKind,
) -> str:
    values = list(dict.fromkeys(grouped[kind]))
    if not values:
        return "not separately specified; see categorized source content"
    return "; ".join(values)


def _carry_validated_findings(section_digests: Sequence[SectionDigest]) -> list[SourceFinding]:
    """Carry immutable evidence atoms through reduction without model transcription."""

    carried: list[SourceFinding] = []
    seen: set[str] = set()
    for digest in section_digests:
        for finding in digest.findings:
            identity = canonical_sha256(finding.model_dump(mode="json"))
            if identity not in seen:
                seen.add(identity)
                carried.append(finding)
    return carried

"""Fresh, runtime-owned source staging for the canonical V2 canary and A/B proofs."""
from __future__ import annotations

from io import BytesIO
import hashlib
import json
import re
from typing import Any, Literal
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

from google.api_core.exceptions import AlreadyExists, PreconditionFailed
from pydantic import Field
from pypdf import PdfReader

from .contracts import NormalizedSource, PlanningRequest, StrictModel


ProofVariant = Literal["canary", "A", "B"]
MAX_ORIGINAL_BYTES = 200 * 1024 * 1024
ROOT_PREFIX = "comunicamundo-v4-rebuild"


class ProofSourceManifest(StrictModel):
    ordinal: int = Field(ge=1)
    source_id: UUID
    title: str
    source_url: str
    original_gcs_uri: str = Field(pattern=r"^gs://")
    pages: int = Field(ge=1)
    bytes: int = Field(ge=1)
    binary_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_text_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    normalized_characters: int = Field(ge=1)


class StagedProofRequest(StrictModel):
    proof: str
    variant: ProofVariant
    project_id: UUID
    request_uri: str = Field(pattern=r"^gs://")
    sources: list[ProofSourceManifest] = Field(min_length=1)


def stage_proof_request(
    variant: ProofVariant,
    *,
    bucket_name: str,
    storage_client: Any,
) -> StagedProofRequest:
    project_id = uuid4()
    definitions = _source_definitions(variant)
    sources: list[NormalizedSource] = []
    manifest: list[ProofSourceManifest] = []
    for ordinal, (title, source_url, extension) in enumerate(definitions, start=1):
        source_id = uuid4()
        original = _download(source_url) if source_url.startswith("http") else _canary_text().encode()
        if not original or len(original) > MAX_ORIGINAL_BYTES:
            raise ValueError(f"source {ordinal} has an aberrant original size")
        try:
            text, pages = _extract_text(original, extension=extension)
        except Exception as error:
            raise ValueError(
                f"source {ordinal} ({title!r}) extraction failed: {error}"
            ) from error
        object_name = f"{ROOT_PREFIX}/projects/{project_id}/sources/{source_id}/original.{extension}"
        original_uri = f"gs://{bucket_name}/{object_name}"
        _persist_immutable(storage_client, bucket_name, object_name, original, _content_type(extension))
        binary_sha = hashlib.sha256(original).hexdigest()
        text_sha = hashlib.sha256(text.encode()).hexdigest()
        metadata = {
            "source_url": source_url,
            "original_gcs_uri": original_uri,
            "binary_sha256": binary_sha,
            "normalized_text_sha256": text_sha,
            "pages": pages,
            "bytes": len(original),
            "normalized_characters": len(text),
            "test_variant": variant,
            "extraction": "runtime text" if extension == "txt" else "pypdf page extraction; no semantic filtering",
        }
        sources.append(
            NormalizedSource(
                source_id=source_id,
                text=text,
                language="pt-BR" if variant == "canary" else "en",
                title=title,
                metadata=metadata,
            )
        )
        manifest.append(
            ProofSourceManifest(
                ordinal=ordinal,
                source_id=source_id,
                title=title,
                source_url=source_url,
                original_gcs_uri=original_uri,
                pages=pages,
                bytes=len(original),
                binary_sha256=binary_sha,
                normalized_text_sha256=text_sha,
                normalized_characters=len(text),
            )
        )
    request = PlanningRequest.model_validate(
        {
            "project_id": project_id,
            "brief": {
                "theme": "O impacto das redes sociais na saúde mental dos adolescentes",
                "approach": "Produza um artigo claro e rigoroso usando as fontes como matéria-prima editorial, distinguindo associação, causalidade, riscos, benefícios e limites.",
                "duration_minutes": 5 if variant == "canary" else 10,
                "output_language": "pt-BR",
                "audience": "Leitores não especialistas interessados em adolescência, saúde mental e vida digital",
                "product": "article",
            },
            "sources": sources,
        }
    )
    request_name = f"{ROOT_PREFIX}/projects/{project_id}/request.json"
    request_data = request.model_dump_json().encode()
    _persist_immutable(storage_client, bucket_name, request_name, request_data, "application/json")
    manifest_name = f"{ROOT_PREFIX}/projects/{project_id}/source-manifest.json"
    manifest_data = json.dumps(
        [item.model_dump(mode="json") for item in manifest], ensure_ascii=False, sort_keys=True
    ).encode()
    _persist_immutable(storage_client, bucket_name, manifest_name, manifest_data, "application/json")
    return StagedProofRequest(
        proof="postgres-canary" if variant == "canary" else "social-media-multisource",
        variant=variant,
        project_id=project_id,
        request_uri=f"gs://{bucket_name}/{request_name}",
        sources=manifest,
    )


def _download(url: str) -> bytes:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 Hefesto-V4-relational-proof"})
    with urlopen(request, timeout=900) as response:
        data = response.read(MAX_ORIGINAL_BYTES + 1)
    return data


def _extract_text(original: bytes, *, extension: str) -> tuple[str, int]:
    if extension == "txt":
        return _normalize(original.decode("utf-8")), 1
    if not original.startswith(b"%PDF"):
        raise ValueError("downloaded source is not a PDF")
    reader = PdfReader(BytesIO(original))
    pages = [_normalize(page.extract_text() or "") for page in reader.pages]
    text = "\n\n".join(page for page in pages if page).strip()
    if not text:
        raise ValueError("PDF text extraction produced no text")
    return text, len(reader.pages)


def _normalize(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    normalized = re.sub(r"[ \t]+\n", "\n", normalized)
    return normalized.strip()


def _persist_immutable(
    storage_client: Any,
    bucket_name: str,
    object_name: str,
    data: bytes,
    content_type: str,
) -> None:
    blob = storage_client.bucket(bucket_name).blob(object_name)
    try:
        blob.upload_from_string(data, content_type=content_type, if_generation_match=0)
    except (AlreadyExists, PreconditionFailed):
        if blob.download_as_bytes() != data:
            raise ValueError(f"conflicting immutable proof input: {object_name}")


def _content_type(extension: str) -> str:
    return "application/pdf" if extension == "pdf" else "text/plain; charset=utf-8"


def _source_definitions(variant: ProofVariant) -> list[tuple[str, str, str]]:
    if variant == "canary":
        return [("Adolescência, vida digital e saúde mental — canário", "generated://hefesto/postgres-canary", "txt")]
    fourth = (
        "Social Media and Adolescent Health — Highlights",
        "https://nap.nationalacademies.org/resource/27396/Highlights_for_Social_Media_and_Adolescent_Health.pdf",
        "pdf",
    ) if variant == "A" else (
        "Social Media and Adolescent Health — Full Report",
        "https://www.ncbi.nlm.nih.gov/books/n/nap27396/pdf/",
        "pdf",
    )
    return [
        ("Social media use in adolescents with and without mental health conditions", "https://www.nature.com/articles/s41562-025-02134-4.pdf", "pdf"),
        ("Mechanisms linking social media use to adolescent mental health vulnerability", "https://www.repository.cam.ac.uk/bitstreams/b3cb127e-41ec-4275-b103-914447cb90b4/download", "pdf"),
        ("Windows of developmental sensitivity to social media", "https://www.repository.cam.ac.uk/bitstreams/d4d58e50-b1d0-4ae8-b8c7-3853c0357002/download", "pdf"),
        fourth,
    ]


def _canary_text() -> str:
    return """ADOLESCÊNCIA, VIDA DIGITAL E SAÚDE MENTAL

Pesquisas observacionais encontram associações entre certos padrões de uso de redes sociais e sintomas de ansiedade ou depressão em adolescentes. Uma associação, isoladamente, não demonstra que a plataforma causou o desfecho: sofrimento prévio também pode alterar a forma de uso.

O efeito médio esconde diferenças importantes. Uso passivo, comparação social, exposição a hostilidade e privação de sono podem aumentar riscos, enquanto contato com pares e acesso a comunidades de apoio podem beneficiar alguns jovens.

Idade, gênero, vulnerabilidades anteriores, contexto familiar e desenho da plataforma modificam os resultados. Por isso, recomendações universais baseadas apenas em tempo de tela têm alcance limitado.

Estudos longitudinais ajudam a ordenar os acontecimentos no tempo, mas ainda enfrentam confundimento e medidas autorrelatadas. Experimentos naturais e dados mais granulares podem fortalecer inferências futuras.

Uma resposta responsável combina educação digital, diálogo familiar, proteção contra assédio e atenção clínica aos sinais de sofrimento. A evidência favorece cuidado proporcional ao risco, não alarmismo nem indiferença."""

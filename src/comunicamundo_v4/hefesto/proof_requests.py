from __future__ import annotations

from io import BytesIO
import hashlib
import re
from urllib.request import Request, urlopen
from uuid import uuid4

from google.cloud import storage
from pypdf import PdfReader

from .contracts import EditorialBrief, NormalizedSource, PlanningRequest
from .storage import persist_json_immutable


MAX_BYTES = 200 * 1024 * 1024


def stage_proof_request(
    variant: str,
    *,
    client: storage.Client,
    bucket_name: str,
) -> tuple[PlanningRequest, str]:
    if variant != "A":
        raise ValueError("only Test A is enabled before product acceptance")
    project_id = uuid4()
    sources: list[NormalizedSource] = []
    for title, url, extension in _definitions(variant):
        original = _download(url)
        text = _extract(original, extension)
        source_id = uuid4()
        original_name = f"hefesto-product/projects/{project_id}/sources/{source_id}/original.{extension}"
        client.bucket(bucket_name).blob(original_name).upload_from_string(
            original,
            content_type="application/pdf" if extension == "pdf" else "text/plain; charset=utf-8",
            if_generation_match=0,
        )
        sources.append(
            NormalizedSource(
                source_id=source_id,
                title=title,
                language="en",
                text=text,
                metadata={
                    "source_url": url,
                    "original_gcs_uri": f"gs://{bucket_name}/{original_name}",
                    "binary_sha256": hashlib.sha256(original).hexdigest(),
                },
            )
        )
    request = PlanningRequest(
        project_id=project_id,
        brief=EditorialBrief(
            theme="O impacto das redes sociais na saúde mental dos adolescentes",
            approach="Produza um artigo claro e rigoroso usando as fontes como matéria-prima editorial, distinguindo associação, causalidade, riscos, benefícios e limites.",
            duration_minutes=10,
            output_language="pt-BR",
            audience="Leitores não especialistas interessados em adolescência, saúde mental e vida digital",
            product="article",
        ),
        sources=sources,
    )
    name = f"hefesto-product/projects/{project_id}/request.json"
    uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=name,
        payload=request.model_dump(mode="json"),
    )
    return request, uri


def _download(url: str) -> bytes:
    with urlopen(Request(url, headers={"User-Agent": "Hefesto-V4"}), timeout=900) as response:
        data = response.read(MAX_BYTES + 1)
    if not data or len(data) > MAX_BYTES:
        raise ValueError("source download has an invalid size")
    return data


def _extract(data: bytes, extension: str) -> str:
    if extension == "txt":
        return data.decode().strip()
    if not data.startswith(b"%PDF"):
        raise ValueError("downloaded source is not a PDF")
    pages = [
        re.sub(r"[ \t]+\n", "\n", page.extract_text(extraction_mode="layout") or "").strip()
        for page in PdfReader(BytesIO(data)).pages
    ]
    text = "\n\n".join(item for item in pages if item).strip()
    if not text:
        raise ValueError("PDF extraction produced no text")
    return text


def _definitions(variant: str) -> list[tuple[str, str, str]]:
    if variant != "A":
        raise ValueError("only Test A is enabled before product acceptance")
    return [
        ("Social media use in adolescents with and without mental health conditions", "https://www.nature.com/articles/s41562-025-02134-4.pdf", "pdf"),
        ("Mechanisms linking social media use to adolescent mental health vulnerability", "https://www.repository.cam.ac.uk/bitstreams/b3cb127e-41ec-4275-b103-914447cb90b4/download", "pdf"),
        ("Windows of developmental sensitivity to social media", "https://www.repository.cam.ac.uk/bitstreams/d4d58e50-b1d0-4ae8-b8c7-3853c0357002/download", "pdf"),
        ("Social Media and Adolescent Health — Highlights", "https://nap.nationalacademies.org/resource/27396/Highlights_for_Social_Media_and_Adolescent_Health.pdf", "pdf"),
    ]

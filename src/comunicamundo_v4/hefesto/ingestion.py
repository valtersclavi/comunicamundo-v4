from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import socket
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid5

from google.cloud import storage
from .contracts import (
    EditorialInputRequest,
    IngestedSource,
    IngestionReceipt,
    NormalizedSource,
    PlanningRequest,
    ProvisionalEditorialProject,
)
from .segmentation import segment_sources
from .normalization import extract_pdf, normalize_pdf_pages
from .storage import parse_gs_uri, persist_bytes_immutable, persist_json_immutable


MAX_SOURCE_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class IngestionResult:
    request: PlanningRequest
    receipt: IngestionReceipt
    receipt_uri: str


class _SafeRedirects(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        _validate_public_https(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SourceReader:
    def __init__(self, client: storage.Client) -> None:
        self._client = client

    def read(self, uri: str) -> bytes:
        if uri.startswith("gs://"):
            bucket, name = parse_gs_uri(uri)
            data = self._client.bucket(bucket).blob(name).download_as_bytes()
        else:
            _validate_public_https(uri)
            opener = build_opener(_SafeRedirects())
            request = Request(uri, headers={"User-Agent": "Hefesto-V4/1.0"})
            with opener.open(request, timeout=900) as response:
                data = response.read(MAX_SOURCE_BYTES + 1)
        if not data:
            raise ValueError("source is empty")
        if len(data) > MAX_SOURCE_BYTES:
            raise ValueError("source exceeds the 200 MB safety limit")
        return data


def ingest(
    input_request: EditorialInputRequest,
    *,
    client: storage.Client,
    bucket_name: str,
    reader: SourceReader | None = None,
    acquirer: object | None = None,
) -> IngestionResult:
    source_reader = reader or SourceReader(client)
    normalized: list[NormalizedSource] = []
    source_receipts: list[IngestedSource] = []
    failed_acquisitions = []
    for ordinal, source_input in enumerate(input_request.sources, start=1):
        if source_input.uri.startswith("https://") and acquirer is not None:
            from .source_pipeline import SourceAcquirer, candidate_identity
            from .contracts import SourceCandidate

            if not isinstance(acquirer, SourceAcquirer):
                raise TypeError("acquirer must be a SourceAcquirer")
            title = source_input.title or source_input.doi or source_input.uri
            candidate = SourceCandidate(
                candidate_id=candidate_identity(
                    doi=source_input.doi, url=source_input.uri
                ),
                provider="provided",
                query="user-provided source",
                rank=1,
                title=title,
                url=source_input.uri,
                acquisition_url=source_input.uri,
                publisher=source_input.publisher,
                doi=source_input.doi,
            )
            payload = acquirer.acquire(candidate)
            if payload.record.status != "acquired":
                failed_acquisitions.append(payload.record)
                continue
            record = payload.record
            text = record.normalized_text or ""
            original = payload.original_bytes or text.encode("utf-8")
            content_type = payload.original_content_type or record.content_type or "text/markdown"
            binary_digest = sha256(original).hexdigest()
            text_digest = sha256(text.encode()).hexdigest()
            source_id = uuid5(
                input_request.project_id,
                f"source:{ordinal}:{record.candidate.candidate_id}:{binary_digest}",
            )
            extension = ".pdf" if content_type == "application/pdf" else ".md"
            original_uri = persist_bytes_immutable(
                client,
                bucket_name=bucket_name,
                name=(
                    f"hefesto-product/projects/{input_request.project_id}/sources/"
                    f"{source_id}/original{extension}"
                ),
                data=original,
                content_type=content_type,
            )
            value = NormalizedSource(
                source_id=source_id,
                title=record.candidate.title,
                language=source_input.language,
                text=text,
                metadata={
                    "input_uri": source_input.uri,
                    "final_url": record.final_url,
                    "original_gcs_uri": original_uri,
                    "content_type": content_type,
                    "bytes": len(original),
                    "binary_sha256": binary_digest,
                    "normalized_text_sha256": text_digest,
                    "normalizer": "hefesto-acquired-text-v1",
                    "acquisition_route": record.selected_route,
                },
            )
            structure = segment_sources([value])
            normalized.append(value)
            source_receipts.append(IngestedSource(
                source_id=source_id,
                source_ordinal=len(source_receipts) + 1,
                input_uri=source_input.uri,
                original_uri=original_uri,
                content_type=content_type,
                bytes=len(original),
                pages=None,
                characters=len(text),
                paragraphs=len(structure),
                sentences=sum(len(item.sentences) for item in structure),
                binary_sha256=binary_digest,
                normalized_text_sha256=text_digest,
                acquisition=record,
            ))
            continue
        original = source_reader.read(source_input.uri)
        binary_digest = sha256(original).hexdigest()
        source_id = uuid5(
            input_request.project_id,
            f"source:{ordinal}:{binary_digest}",
        )
        text, pages = extract_pdf(original)
        original_name = (
            f"hefesto-product/projects/{input_request.project_id}/sources/"
            f"{source_id}/original.pdf"
        )
        original_uri = persist_bytes_immutable(
            client,
            bucket_name=bucket_name,
            name=original_name,
            data=original,
            content_type="application/pdf",
        )
        value = NormalizedSource(
            source_id=source_id,
            title=source_input.title,
            language=source_input.language,
            text=text,
            metadata={
                "input_uri": source_input.uri,
                "original_gcs_uri": original_uri,
                "content_type": "application/pdf",
                "pages": pages,
                "bytes": len(original),
                "binary_sha256": binary_digest,
                "normalized_text_sha256": sha256(text.encode()).hexdigest(),
                "normalizer": "hefesto-pdf-v1",
            },
        )
        structure = segment_sources([value])
        normalized.append(value)
        source_receipts.append(
            IngestedSource(
                source_id=source_id,
                source_ordinal=len(source_receipts) + 1,
                input_uri=source_input.uri,
                original_uri=original_uri,
                content_type="application/pdf",
                bytes=len(original),
                pages=pages,
                characters=len(text),
                paragraphs=len(structure),
                sentences=sum(len(item.sentences) for item in structure),
                binary_sha256=binary_digest,
                normalized_text_sha256=sha256(text.encode()).hexdigest(),
            )
        )
    if not normalized:
        raise ValueError("none of the supplied sources could be acquired and normalized")
    request = PlanningRequest(
        project_id=input_request.project_id,
        brief=input_request.brief,
        sources=normalized,
    )
    request_name = (
        f"hefesto-product/projects/{input_request.project_id}/normalized-request.json"
    )
    request_uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=request_name,
        payload=request.model_dump(mode="json"),
    )
    receipt = IngestionReceipt(
        status="normalized",
        project_id=input_request.project_id,
        normalized_request_uri=request_uri,
        source_count=len(source_receipts),
        paragraph_count=sum(item.paragraphs for item in source_receipts),
        sentence_count=sum(item.sentences for item in source_receipts),
        model_invocations=0,
        sources=source_receipts,
        failed_acquisitions=failed_acquisitions,
    )
    receipt_uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=f"hefesto-product/projects/{input_request.project_id}/ingestion-receipt.json",
        payload=receipt.model_dump(mode="json"),
    )
    return IngestionResult(request=request, receipt=receipt, receipt_uri=receipt_uri)


def ingest_acquired(
    input_request: EditorialInputRequest,
    acquisitions: list[object],
    *,
    client: storage.Client,
    bucket_name: str,
    provisional_project: ProvisionalEditorialProject,
    supported_subject_indexes: list[int],
    subject_coverage: dict[str, list[int]],
) -> IngestionResult:
    """Redigest successfully acquired public sources into the frozen core input."""
    # Local import keeps the normalization layer independent of transport routes.
    from .source_pipeline import AcquisitionPayload

    payloads = [item for item in acquisitions if isinstance(item, AcquisitionPayload)]
    successful = [item for item in payloads if item.record.status == "acquired"]
    failures = [item.record for item in payloads if item.record.status != "acquired"]
    if not successful:
        raise ValueError("none of the editor-selected sources could be acquired")

    normalized: list[NormalizedSource] = []
    source_receipts: list[IngestedSource] = []
    for ordinal, payload in enumerate(successful, start=1):
        record = payload.record
        text = record.normalized_text or ""
        if not text.strip():
            raise ValueError("an acquired source has no normalized text")
        original = payload.original_bytes or text.encode("utf-8")
        content_type = payload.original_content_type or record.content_type or "text/markdown"
        binary_digest = sha256(original).hexdigest()
        text_digest = sha256(text.encode()).hexdigest()
        source_id = uuid5(
            input_request.project_id,
            f"acquired:{record.candidate.candidate_id}:{binary_digest}",
        )
        extension = ".pdf" if content_type == "application/pdf" else ".md"
        original_uri = persist_bytes_immutable(
            client,
            bucket_name=bucket_name,
            name=(
                f"hefesto-product/projects/{input_request.project_id}/sources/"
                f"{source_id}/original{extension}"
            ),
            data=original,
            content_type=content_type,
        )
        value = NormalizedSource(
            source_id=source_id,
            title=record.candidate.title,
            language=input_request.brief.output_language,
            text=text,
            metadata={
                "input_uri": record.candidate.url,
                "final_url": record.final_url,
                "doi": record.candidate.doi,
                "publisher": record.candidate.publisher,
                "original_gcs_uri": original_uri,
                "content_type": content_type,
                "bytes": len(original),
                "binary_sha256": binary_digest,
                "normalized_text_sha256": text_digest,
                "normalizer": "hefesto-acquired-text-v1",
                "acquisition_route": record.selected_route,
                "covered_subject_indexes": subject_coverage.get(
                    record.candidate.candidate_id, []
                ),
            },
        )
        structure = segment_sources([value])
        normalized.append(value)
        source_receipts.append(IngestedSource(
            source_id=source_id,
            source_ordinal=ordinal,
            input_uri=record.candidate.url,
            original_uri=original_uri,
            content_type=content_type,
            bytes=len(original),
            pages=None,
            characters=len(text),
            paragraphs=len(structure),
            sentences=sum(len(item.sentences) for item in structure),
            binary_sha256=binary_digest,
            normalized_text_sha256=text_digest,
            acquisition=record,
        ))

    request = PlanningRequest(
        project_id=input_request.project_id,
        brief=input_request.brief,
        sources=normalized,
        provisional_project=provisional_project,
        supported_provisional_subject_indexes=supported_subject_indexes,
    )
    request_uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=f"hefesto-product/projects/{input_request.project_id}/normalized-request.json",
        payload=request.model_dump(mode="json"),
    )
    receipt = IngestionReceipt(
        status="redigested",
        project_id=input_request.project_id,
        normalized_request_uri=request_uri,
        source_count=len(source_receipts),
        paragraph_count=sum(item.paragraphs for item in source_receipts),
        sentence_count=sum(item.sentences for item in source_receipts),
        model_invocations=0,
        sources=source_receipts,
        failed_acquisitions=failures,
    )
    receipt_uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=f"hefesto-product/projects/{input_request.project_id}/ingestion-receipt.json",
        payload=receipt.model_dump(mode="json"),
    )
    return IngestionResult(request=request, receipt=receipt, receipt_uri=receipt_uri)


def _validate_public_https(uri: str) -> None:
    parsed = urlparse(uri)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError("remote sources must use a public HTTPS URL")
    try:
        addresses = {
            item[4][0] for item in socket.getaddrinfo(parsed.hostname, parsed.port or 443)
        }
    except socket.gaierror as error:
        raise ValueError("source hostname cannot be resolved") from error
    if not addresses or any(not ipaddress.ip_address(value).is_global for value in addresses):
        raise ValueError("source URL does not resolve exclusively to public addresses")

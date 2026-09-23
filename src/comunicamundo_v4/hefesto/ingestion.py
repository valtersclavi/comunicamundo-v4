from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
import ipaddress
import socket
from typing import Callable
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID, uuid5

from google.cloud import storage
from .contracts import (
    AcquiredSource,
    EditorialInputRequest,
    IngestedSource,
    IngestionReceipt,
    NormalizedSource,
    PlanningRequest,
    ProvisionalEditorialProject,
    SourceNormalizationFailure,
)
from .document_formats import (
    MAX_DOCUMENT_BYTES,
    is_docling_format,
    resolve_document_format,
    source_file_name,
)
from .document_normalization import normalize_document
from .segmentation import segment_sources
from .normalization import extract_pdf, normalize_pdf_pages
from .storage import parse_gs_uri, persist_bytes_immutable, persist_json_immutable


MAX_SOURCE_BYTES = MAX_DOCUMENT_BYTES


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
    on_progress: Callable[[dict[str, object]], None] | None = None,
) -> IngestionResult:
    source_reader = reader or SourceReader(client)
    normalized: list[NormalizedSource] = []
    source_receipts: list[IngestedSource] = []
    failed_acquisitions = []
    failed_normalizations: list[SourceNormalizationFailure] = []
    for ordinal, source_input in enumerate(input_request.sources, start=1):
        failed_step = "leitura ou aquisição"
        def progress(status: str, **details: object) -> None:
            if on_progress is not None:
                on_progress({
                    "ordinal": ordinal, "input_uri": source_input.uri,
                    "title": source_input.title, "status": status, **details,
                })

        progress("reading")
        try:
            acquisition = None
            if source_input.uri.startswith("https://") and acquirer is not None:
                from .source_pipeline import SourceAcquirer, candidate_identity
                from .contracts import SourceCandidate

                if not isinstance(acquirer, SourceAcquirer):
                    raise TypeError("acquirer must be a SourceAcquirer")
                candidate = SourceCandidate(
                    candidate_id=candidate_identity(
                        doi=source_input.doi, url=source_input.uri
                    ),
                    provider="provided",
                    query="user-provided source",
                    rank=1,
                    title=source_input.title or source_input.doi or source_input.uri,
                    url=source_input.uri,
                    acquisition_url=source_input.uri,
                    publisher=source_input.publisher,
                    doi=source_input.doi,
                )
                payload = acquirer.acquire(candidate)
                if payload.record.status != "acquired":
                    failed_acquisitions.append(payload.record)
                    progress("failed", failed_step=failed_step,
                             error_class="AcquisitionFailed",
                             error_message=payload.record.failure_reason or "Aquisição falhou")
                    continue
                acquisition = payload.record
                acquired_text = acquisition.normalized_text or ""
                original = payload.original_bytes or acquired_text.encode("utf-8")
                content_type = (
                    payload.original_content_type
                    or acquisition.content_type
                    or "text/markdown"
                )
            else:
                file_name = source_file_name(
                    source_input.uri, source_input.title, source_input.content_type
                )
                original = source_reader.read(source_input.uri)
                document_format = resolve_document_format(
                    file_name, source_input.content_type
                )
                content_type = document_format.content_type
                acquired_text = (
                    original.decode("utf-8-sig")
                    if document_format.family == "text" else ""
                )

            failed_step = "normalização e divisão em parágrafos"
            progress("normalizing")
            title = (
                acquisition.candidate.title if acquisition is not None
                else source_input.title
            )
            file_name = source_file_name(source_input.uri, title, content_type)
            document_format = None
            try:
                document_format = resolve_document_format(file_name, content_type)
            except ValueError:
                # Extracted web pages from acquisition routes are already canonical text.
                if not acquired_text:
                    raise
            if (
                acquisition is not None
                and acquired_text
                and acquisition.selected_route != "direct"
            ):
                # You/Jina/Browser already returned the canonical readable body.
                document_format = None

            binary_digest = sha256(original).hexdigest()
            identity = (
                acquisition.candidate.candidate_id if acquisition is not None else binary_digest
            )
            source_id = uuid5(
                input_request.project_id,
                f"source:{ordinal}:{identity}:{binary_digest}",
            )
            if document_format is not None and is_docling_format(document_format):
                value, source_receipt = _normalize_document_source(
                    input_request=input_request,
                    source_id=source_id,
                    source_ordinal=len(normalized) + 1,
                    input_uri=source_input.uri,
                    title=title,
                    language=source_input.language,
                    original=original,
                    file_name=file_name,
                    content_type=document_format.content_type,
                    client=client,
                    bucket_name=bucket_name,
                    acquisition=acquisition,
                )
            else:
                value, source_receipt = _normalize_text_source(
                    input_request=input_request,
                    source_id=source_id,
                    source_ordinal=len(normalized) + 1,
                    input_uri=source_input.uri,
                    title=acquisition.candidate.title if acquisition else source_input.title,
                    language=source_input.language,
                    text=acquired_text,
                    original=original,
                    file_name=file_name,
                    content_type=content_type,
                    client=client,
                    bucket_name=bucket_name,
                    acquisition=acquisition,
                )
            normalized.append(value)
            source_receipts.append(source_receipt)
            progress("normalized", paragraphs=source_receipt.paragraphs,
                     sentences=source_receipt.sentences, pages=source_receipt.pages)
        except Exception as error:
            failed_normalizations.append(SourceNormalizationFailure(
                input_uri=source_input.uri,
                title=source_input.title,
                failed_step=failed_step,
                error_class=type(error).__name__,
                error_message=str(error)[:2000],
            ))
            progress("failed", failed_step=failed_step,
                     error_class=type(error).__name__,
                     error_message=str(error)[:2000])
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
        failed_normalizations=failed_normalizations,
    )
    receipt_uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=f"hefesto-product/projects/{input_request.project_id}/ingestion-receipt.json",
        payload=receipt.model_dump(mode="json"),
    )
    return IngestionResult(request=request, receipt=receipt, receipt_uri=receipt_uri)


def _normalize_document_source(
    *,
    input_request: EditorialInputRequest,
    source_id: UUID,
    source_ordinal: int,
    input_uri: str,
    title: str | None,
    language: str,
    original: bytes,
    file_name: str,
    content_type: str,
    client: storage.Client,
    bucket_name: str,
    acquisition: AcquiredSource | None,
) -> tuple[NormalizedSource, IngestedSource]:
    document_format = resolve_document_format(file_name, content_type)
    original_uri = persist_bytes_immutable(
        client,
        bucket_name=bucket_name,
        name=(
            f"hefesto-product/projects/{input_request.project_id}/sources/"
            f"{source_id}/original{document_format.extension}"
        ),
        data=original,
        content_type=document_format.content_type,
    )
    conversion = normalize_document(
        original,
        file_name=file_name,
        content_type=document_format.content_type,
        project_id=input_request.project_id,
        source_id=source_id,
        source_ordinal=source_ordinal,
        title=title,
        language=language,
        input_uri=input_uri,
        original_uri=original_uri,
        client=client,
        bucket_name=bucket_name,
    )
    value = conversion.source
    if acquisition is not None:
        value = value.model_copy(update={"metadata": {
            **value.metadata,
            "final_url": acquisition.final_url,
            "doi": acquisition.candidate.doi,
            "publisher": acquisition.candidate.publisher,
            "acquisition_route": acquisition.selected_route,
        }})
    structure = segment_sources([value])
    binary_digest = sha256(original).hexdigest()
    text_digest = sha256(value.text.encode()).hexdigest()
    return value, IngestedSource(
        source_id=value.source_id,
        source_ordinal=source_ordinal,
        input_uri=input_uri,
        original_uri=original_uri,
        content_type=document_format.content_type,
        bytes=len(original),
        pages=conversion.pages or None,
        characters=len(value.text),
        paragraphs=len(structure),
        sentences=sum(len(item.sentences) for item in structure),
        tables=sum(item.kind == "table" for item in value.assets),
        figures=sum(item.kind != "table" for item in value.assets),
        normalization_status=conversion.status,
        normalization_receipt_uri=conversion.receipt_uri,
        binary_sha256=binary_digest,
        normalized_text_sha256=text_digest,
        acquisition=acquisition,
    )


def _normalize_text_source(
    *,
    input_request: EditorialInputRequest,
    source_id: UUID,
    source_ordinal: int,
    input_uri: str,
    title: str | None,
    language: str,
    text: str,
    original: bytes,
    file_name: str,
    content_type: str,
    client: storage.Client,
    bucket_name: str,
    acquisition: AcquiredSource | None,
) -> tuple[NormalizedSource, IngestedSource]:
    if not text.strip():
        raise ValueError("source has no usable text")
    binary_digest = sha256(original).hexdigest()
    text_digest = sha256(text.encode()).hexdigest()
    try:
        original_extension = resolve_document_format(file_name, content_type).extension
    except ValueError:
        original_extension = ".md"
    original_uri = persist_bytes_immutable(
        client,
        bucket_name=bucket_name,
        name=(
            f"hefesto-product/projects/{input_request.project_id}/sources/"
            f"{source_id}/original{original_extension}"
        ),
        data=original,
        content_type=content_type,
    )
    value = NormalizedSource(
        source_id=source_id,
        title=title,
        language=language,
        text=text,
        metadata={
            "input_uri": input_uri,
            "final_url": acquisition.final_url if acquisition else None,
            "doi": acquisition.candidate.doi if acquisition else None,
            "publisher": acquisition.candidate.publisher if acquisition else None,
            "original_gcs_uri": original_uri,
            "content_type": content_type,
            "bytes": len(original),
            "binary_sha256": binary_digest,
            "normalized_text_sha256": text_digest,
            "normalizer": "hefesto-lossless-text-v1",
            "acquisition_route": acquisition.selected_route if acquisition else None,
        },
    )
    structure = segment_sources([value])
    return value, IngestedSource(
        source_id=value.source_id,
        source_ordinal=source_ordinal,
        input_uri=input_uri,
        original_uri=original_uri,
        content_type=content_type,
        bytes=len(original),
        pages=None,
        characters=len(text),
        paragraphs=len(structure),
        sentences=sum(len(item.sentences) for item in structure),
        binary_sha256=binary_digest,
        normalized_text_sha256=text_digest,
        acquisition=acquisition,
    )


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
    failed_normalizations: list[SourceNormalizationFailure] = []
    for ordinal, payload in enumerate(successful, start=1):
        record = payload.record
        text = record.normalized_text or ""
        original = payload.original_bytes or text.encode("utf-8")
        content_type = payload.original_content_type or record.content_type or "text/markdown"
        file_name = source_file_name(
            record.candidate.url, record.candidate.title, content_type
        )
        document_format = None
        try:
            document_format = resolve_document_format(file_name, content_type)
        except ValueError:
            document_format = None
        if text and record.selected_route != "direct":
            document_format = None
        binary_digest = sha256(original).hexdigest()
        source_id = uuid5(
            input_request.project_id,
            f"acquired:{record.candidate.candidate_id}:{binary_digest}",
        )
        try:
            if document_format is not None and is_docling_format(document_format):
                value, source_receipt = _normalize_document_source(
                    input_request=input_request,
                    source_id=source_id,
                    source_ordinal=len(normalized) + 1,
                    input_uri=record.candidate.url,
                    title=record.candidate.title,
                    language=input_request.brief.output_language,
                    original=original,
                    file_name=file_name,
                    content_type=document_format.content_type,
                    client=client,
                    bucket_name=bucket_name,
                    acquisition=record,
                )
            else:
                value, source_receipt = _normalize_text_source(
                    input_request=input_request,
                    source_id=source_id,
                    source_ordinal=len(normalized) + 1,
                    input_uri=record.candidate.url,
                    title=record.candidate.title,
                    language=input_request.brief.output_language,
                    text=text,
                    original=original,
                    file_name=file_name,
                    content_type=content_type,
                    client=client,
                    bucket_name=bucket_name,
                    acquisition=record,
                )
            value = value.model_copy(update={"metadata": {
                **value.metadata,
                "covered_subject_indexes": subject_coverage.get(
                    record.candidate.candidate_id, []
                ),
            }})
            normalized.append(value)
            source_receipts.append(source_receipt)
        except Exception as error:
            failed_normalizations.append(SourceNormalizationFailure(
                input_uri=record.candidate.url,
                title=record.candidate.title,
                error_class=type(error).__name__,
                error_message=str(error)[:2000],
            ))

    if not normalized:
        raise ValueError("none of the acquired sources could be normalized")

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
        failed_normalizations=failed_normalizations,
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

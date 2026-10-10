from __future__ import annotations

from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor, as_completed
from hashlib import sha256
import ipaddress
import socket
from threading import Lock
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
            request = Request(uri, headers={"User-Agent": "Hefesto-V8/1.0"})
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
    on_normalized: Callable[[int, NormalizedSource, IngestedSource], None] | None = None,
) -> IngestionResult:
    source_reader = reader or SourceReader(client)
    results: dict[int, tuple[NormalizedSource, IngestedSource]] = {}
    failed_acquisitions = []
    failed_normalizations: list[SourceNormalizationFailure] = []
    heavy_converter_lock = Lock()

    def process_source(ordinal: int, source_input: object):
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
                    progress("failed", failed_step=failed_step,
                             error_class="AcquisitionFailed",
                             error_message=payload.record.failure_reason or "Aquisição falhou")
                    return None, None, payload.record, None
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
                def convert():
                    return _normalize_document_source(
                        input_request=input_request,
                        source_id=source_id, source_ordinal=ordinal,
                        input_uri=source_input.uri, title=title,
                        language=source_input.language, original=original,
                        file_name=file_name,
                        content_type=document_format.content_type,
                        client=client, bucket_name=bucket_name,
                        acquisition=acquisition,
                        on_page=lambda done, total: progress(
                            "extracting", pages_done=done, pages=total,
                        ),
                    )
                if document_format.extension == ".pdf":
                    value, source_receipt = convert()
                else:
                    # Keep memory-heavy Docling conversions to one at a time;
                    # native PDFs and other sources continue concurrently.
                    with heavy_converter_lock:
                        value, source_receipt = convert()
            else:
                value, source_receipt = _normalize_text_source(
                    input_request=input_request,
                    source_id=source_id,
                    source_ordinal=ordinal,
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
            progress("normalized", paragraphs=source_receipt.paragraphs,
                     sentences=source_receipt.sentences, pages=source_receipt.pages)
            return value, source_receipt, None, None
        except Exception as error:
            failure = SourceNormalizationFailure(
                input_uri=source_input.uri,
                title=source_input.title,
                failed_step=failed_step,
                error_class=type(error).__name__,
                error_message=str(error)[:2000],
            )
            progress("failed", failed_step=failed_step,
                     error_class=type(error).__name__,
                     error_message=str(error)[:2000])
            return None, None, None, failure

    with ThreadPoolExecutor(max_workers=min(4, len(input_request.sources))) as pool:
        futures = {
            pool.submit(process_source, ordinal, source): ordinal
            for ordinal, source in enumerate(input_request.sources, start=1)
        }
        pending: dict[int, tuple[NormalizedSource | None, IngestedSource | None]] = {}
        next_ordinal = 1
        for future in as_completed(futures):
            value, source_receipt, acquisition_failure, failure = future.result()
            if value is not None and source_receipt is not None:
                results[futures[future]] = value, source_receipt
            pending[futures[future]] = value, source_receipt
            if acquisition_failure is not None:
                failed_acquisitions.append(acquisition_failure)
            if failure is not None:
                failed_normalizations.append(failure)
            while next_ordinal in pending:
                ready_source, ready_receipt = pending.pop(next_ordinal)
                if ready_source is not None and ready_receipt is not None and on_normalized:
                    on_normalized(next_ordinal, ready_source, ready_receipt)
                next_ordinal += 1
    normalized = [item[0] for _, item in sorted(results.items())]
    source_receipts = [item[1] for _, item in sorted(results.items())]
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
    on_page: Callable[[int, int], None] | None = None,
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
        on_page=on_page,
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

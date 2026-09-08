from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from hashlib import sha256
from io import BytesIO
import ipaddress
import re
import socket
from urllib.parse import urlparse
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import uuid5

from google.cloud import storage
from pypdf import PdfReader

from .contracts import (
    EditorialInputRequest,
    IngestedSource,
    IngestionReceipt,
    NormalizedSource,
    PlanningRequest,
)
from .segmentation import segment_sources
from .storage import parse_gs_uri, persist_bytes_immutable, persist_json_immutable


MAX_SOURCE_BYTES = 200 * 1024 * 1024
_BLANKS = re.compile(r"\n[ \t]*\n+")
_SPACES = re.compile(r"[ \t]+")
_HYPHENATED_WRAP = re.compile(r"(?<=\w)-\s*\n\s*(?=[a-zà-öø-ÿ])")


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
) -> IngestionResult:
    source_reader = reader or SourceReader(client)
    normalized: list[NormalizedSource] = []
    source_receipts: list[IngestedSource] = []
    for ordinal, source_input in enumerate(input_request.sources, start=1):
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
                source_ordinal=ordinal,
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
    )
    receipt_uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=f"hefesto-product/projects/{input_request.project_id}/ingestion-receipt.json",
        payload=receipt.model_dump(mode="json"),
    )
    return IngestionResult(request=request, receipt=receipt, receipt_uri=receipt_uri)


def extract_pdf(data: bytes) -> tuple[str, int]:
    if not data.startswith(b"%PDF"):
        raise ValueError("source is not a PDF")
    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted:
        try:
            if not reader.decrypt(""):
                raise ValueError("password required")
        except Exception as error:
            raise ValueError("encrypted PDF cannot be read") from error
    raw_pages = [
        (page.extract_text(extraction_mode="layout") or "").replace("\r", "")
        for page in reader.pages
    ]
    text = normalize_pdf_pages(raw_pages)
    if not text:
        raise ValueError("PDF contains no extractable text; OCR is required")
    return text, len(reader.pages)


def normalize_pdf_pages(raw_pages: list[str]) -> str:
    pages = [_clean_lines(value) for value in raw_pages]
    repeated = _repeated_boundaries(pages)
    paragraphs: list[str] = []
    for lines in pages:
        kept = [line for line in lines if _canonical_line(line) not in repeated]
        page_text = "\n".join(kept).strip()
        for raw_block in _BLANKS.split(page_text):
            block = _join_wrapped_lines(raw_block)
            if block:
                paragraphs.append(block)
    return "\n\n".join(paragraphs).strip()


def _clean_lines(value: str) -> list[str]:
    return [line.rstrip() for line in value.splitlines()]


def _repeated_boundaries(pages: list[list[str]]) -> set[str]:
    candidates: list[str] = []
    for lines in pages:
        nonempty = [line for line in lines if line.strip()]
        if nonempty:
            candidates.extend((_canonical_line(nonempty[0]), _canonical_line(nonempty[-1])))
    threshold = max(3, (len(pages) + 1) // 2)
    return {
        value for value, count in Counter(candidates).items()
        if value and count >= threshold
    }


def _canonical_line(value: str) -> str:
    return _SPACES.sub(" ", value.strip())


def _join_wrapped_lines(value: str) -> str:
    value = _HYPHENATED_WRAP.sub("", value.strip())
    lines = [_canonical_line(line) for line in value.splitlines() if line.strip()]
    return " ".join(lines).strip()


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

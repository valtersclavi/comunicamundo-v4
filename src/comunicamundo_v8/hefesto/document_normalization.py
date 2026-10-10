from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
import json
import os
from pathlib import Path
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from google.cloud import storage

from .contracts import (
    BoundingBox,
    NormalizedSource,
    Paragraph,
    ParagraphAssetLink,
    SourceAsset,
    content_hash,
)
from .document_formats import MAX_DOCUMENT_BYTES, resolve_document_format
from .segmentation import _sentences
from .normalization import pdf_page_blocks
from .storage import (
    persist_bytes_immutable,
    persist_json_immutable,
    persist_text_immutable,
)


@dataclass(frozen=True, slots=True)
class DocumentNormalization:
    source: NormalizedSource
    status: str
    pages: int
    receipt_uri: str


@dataclass(frozen=True, slots=True)
class _BodyEntry:
    position: int
    paragraph_id: str | None = None
    asset_id: str | None = None
    is_caption: bool = False


@lru_cache(maxsize=1)
def _document_converter() -> Any:
    """Load Docling only in the worker process and reuse every pipeline."""
    from docling.datamodel import asr_model_specs
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        AsrPipelineOptions,
        PdfPipelineOptions,
        TesseractCliOcrOptions,
        VideoPipelineOptions,
    )
    from docling.document_converter import (
        AudioFormatOption,
        DocumentConverter,
        ImageFormatOption,
        PdfFormatOption,
        VideoFormatOption,
    )
    from docling.pipeline.asr_pipeline import AsrPipeline

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.ocr_options = TesseractCliOcrOptions(lang=[])
    options.do_table_structure = False
    options.generate_page_images = False
    options.generate_picture_images = False
    options.images_scale = float(os.environ.get("HEFESTO_DOCLING_IMAGE_SCALE", "2.0"))
    options.document_timeout = float(
        os.environ.get("HEFESTO_DOCLING_DOCUMENT_TIMEOUT", "600")
    )
    artifacts = os.environ.get("HEFESTO_DOCLING_ARTIFACTS_PATH", "").strip()
    if artifacts:
        options.artifacts_path = Path(artifacts)
    asr_options = AsrPipelineOptions()
    asr_options.asr_options = asr_model_specs.WHISPER_SMALL_NATIVE
    asr_options.document_timeout = float(
        os.environ.get("HEFESTO_DOCLING_MEDIA_TIMEOUT", "3600")
    )
    video_options = VideoPipelineOptions(
        asr_options=asr_model_specs.WHISPER_SMALL_NATIVE,
        enable_diarization=False,
        document_timeout=float(
            os.environ.get("HEFESTO_DOCLING_MEDIA_TIMEOUT", "3600")
        ),
    )
    allowed_names = (
        "PDF", "DOCX", "PPTX", "XLSX", "HTML", "MD", "EPUB", "IMAGE",
        "AUDIO", "VIDEO",
    )
    allowed_formats = [
        getattr(InputFormat, name) for name in allowed_names
        if hasattr(InputFormat, name)
    ]
    return DocumentConverter(
        allowed_formats=allowed_formats,
        format_options={
            InputFormat.PDF: PdfFormatOption(pipeline_options=options),
            InputFormat.IMAGE: ImageFormatOption(pipeline_options=options),
            InputFormat.AUDIO: AudioFormatOption(
                pipeline_cls=AsrPipeline,
                pipeline_options=asr_options,
            ),
            InputFormat.VIDEO: VideoFormatOption(pipeline_options=video_options),
        },
    )


def normalize_document(
    data: bytes,
    *,
    file_name: str,
    content_type: str,
    project_id: UUID,
    source_id: UUID,
    source_ordinal: int,
    title: str | None,
    language: str,
    input_uri: str,
    original_uri: str,
    client: storage.Client,
    bucket_name: str,
    on_page: Callable[[int, int], None] | None = None,
) -> DocumentNormalization:
    document_format = resolve_document_format(file_name, content_type)
    if document_format.family == "text":
        raise ValueError("plain text must use the lossless text normalizer")
    if document_format.extension == ".pdf" and not data.startswith(b"%PDF"):
        raise ValueError("source declares PDF but does not contain a PDF signature")
    if document_format.extension == ".pdf":
        try:
            return _normalize_native_pdf(
                data, project_id=project_id, source_id=source_id,
                source_ordinal=source_ordinal, title=title, language=language,
                input_uri=input_uri, original_uri=original_uri, client=client,
                bucket_name=bucket_name, on_page=on_page,
            )
        except ValueError as error:
            if str(error) != "PDF has no native text; OCR is required":
                raise
            # A wholly image-based PDF needs OCR. Sparse textual pages do not.

    from docling.datamodel.base_models import DocumentStream

    result = _document_converter().convert(
        DocumentStream(name=file_name, stream=BytesIO(data)),
        raises_on_error=False,
        max_file_size=MAX_DOCUMENT_BYTES,
    )
    status_name = _status_name(result.status)
    if status_name != "success":
        raise ValueError(_conversion_failure(result, status_name))

    document = result.document
    prefix = f"hefesto-product/projects/{project_id}/sources/{source_id}"
    paragraphs: list[Paragraph] = []
    assets: list[SourceAsset] = []
    entries: list[_BodyEntry] = []
    canonical_blocks: list[str] = []
    cursor = 0
    asset_ordinal = 0

    def append_block(value: str) -> tuple[int, int]:
        nonlocal cursor
        if canonical_blocks:
            cursor += 2
        start = cursor
        canonical_blocks.append(value)
        cursor += len(value)
        return start, cursor

    for position, (item, _level) in enumerate(
        document.iterate_items(traverse_pictures=True)
    ):
        item_type = type(item).__name__
        origin_ref = str(getattr(item, "self_ref", "")) or f"item-{position}"
        label = _label(item)
        page_no, bbox = _provenance(item)

        if item_type == "TableItem":
            asset_ordinal += 1
            readable = _table_export(item, document, "markdown")
            caption = _caption(item, document)
            asset_id = content_hash(
                f"{source_id}:{origin_ref}:table:{content_hash(readable or caption or origin_ref)}"
            )
            assets.append(SourceAsset(
                asset_id=asset_id,
                source_id=source_id,
                source_ordinal=source_ordinal,
                ordinal=asset_ordinal,
                kind="table",
                role="content",
                page_no=page_no,
                bbox=bbox,
                caption=caption,
                text_representation=readable or caption,
                origin_ref=origin_ref,
                payload={"docling_label": label},
            ))
            marker = f"[asset:{asset_id}]"
            append_block(marker)
            entries.append(_BodyEntry(position=position * 2, asset_id=asset_id))
            table_text = "\n".join(
                part for part in (caption, readable) if part and part.strip()
            ).strip()
            if table_text:
                start, end = append_block(table_text)
                paragraph = _paragraph(
                    source_id=source_id,
                    source_ordinal=source_ordinal,
                    ordinal=len(paragraphs) + 1,
                    text=table_text,
                    element_type="table",
                    page_no=page_no,
                    bbox=bbox,
                    canonical_start=start,
                    canonical_end=end,
                    origin_ref=origin_ref,
                )
                paragraphs.append(paragraph)
                entries.append(_BodyEntry(
                    position=position * 2 + 1,
                    paragraph_id=paragraph.paragraph_id,
                    is_caption=bool(caption),
                ))
            continue

        if item_type == "PictureItem":
            if _is_full_page_ocr_picture(item, document, page_no, bbox):
                continue
            asset_ordinal += 1
            caption = _caption(item, document)
            asset_id = content_hash(
                f"{source_id}:{origin_ref}:picture:{content_hash(caption or origin_ref)}"
            )
            kind = "chart" if "chart" in label else "figure"
            role = "decorative" if label in {"logo", "icon", "decoration"} else "unknown"
            assets.append(SourceAsset(
                asset_id=asset_id,
                source_id=source_id,
                source_ordinal=source_ordinal,
                ordinal=asset_ordinal,
                kind=kind,
                role=role,
                page_no=page_no,
                bbox=bbox,
                caption=caption,
                text_representation=caption,
                origin_ref=origin_ref,
                payload={"docling_label": label},
            ))
            append_block(f"[asset:{asset_id}]")
            entries.append(_BodyEntry(position=position * 2, asset_id=asset_id))
            continue

        text = str(getattr(item, "text", "") or "").strip()
        if not text or _is_furniture(item):
            continue
        if _element_type(label) == "list_item" and not text.startswith(("- ", "• ")):
            text = f"- {text}"
        start, end = append_block(text)
        paragraph = _paragraph(
            source_id=source_id,
            source_ordinal=source_ordinal,
            ordinal=len(paragraphs) + 1,
            text=text,
            element_type=_element_type(label),
            page_no=page_no,
            bbox=bbox,
            canonical_start=start,
            canonical_end=end,
            origin_ref=origin_ref,
        )
        paragraphs.append(paragraph)
        entries.append(_BodyEntry(
            position=position * 2,
            paragraph_id=paragraph.paragraph_id,
            is_caption=_element_type(label) == "caption",
        ))

    if not paragraphs:
        raise ValueError("document contains no usable text after structured conversion")

    canonical_text = "\n\n".join(canonical_blocks).strip()
    asset_links = _asset_links(entries)
    canonical_uri = persist_text_immutable(
        client,
        bucket_name=bucket_name,
        name=f"{prefix}/canonical/content.txt",
        text=canonical_text,
    )
    document_uri = None
    pages = len(getattr(document, "pages", {}) or {})
    normalized_status = "partial" if status_name == "partial_success" else "normalized"
    metadata: dict[str, object] = {
        "input_uri": input_uri,
        "original_gcs_uri": original_uri,
        "content_type": document_format.content_type,
        "input_format": document_format.extension.removeprefix("."),
        "pages": pages,
        "bytes": len(data),
        "binary_sha256": sha256(data).hexdigest(),
        "normalized_text_sha256": sha256(canonical_text.encode()).hexdigest(),
        "normalizer": "docling-document-v1",
        "canonical_text_uri": canonical_uri,
        "docling_document_uri": document_uri,
        "normalization_status": normalized_status,
        "docling_version": _package_version("docling"),
        "docling_core_version": _package_version("docling-core"),
    }
    source = NormalizedSource(
        source_id=source_id,
        title=title,
        language=language,
        text=canonical_text,
        metadata=metadata,
        paragraphs=paragraphs,
        assets=assets,
        asset_links=asset_links,
    )
    receipt_payload: dict[str, object] = {
        "status": normalized_status,
        "source_id": str(source_id),
        "normalizer": "docling-document-v1",
        "input_format": document_format.extension.removeprefix("."),
        "content_type": document_format.content_type,
        "docling_version": _package_version("docling"),
        "docling_core_version": _package_version("docling-core"),
        "configuration": {
            "ocr_engine": "tesseract-cli",
            "ocr_language": "automatic-script-detection",
            "table_structure": False,
            "picture_images": False,
            "image_scale": float(os.environ.get("HEFESTO_DOCLING_IMAGE_SCALE", "2.0")),
            "document_timeout_seconds": float(
                os.environ.get("HEFESTO_DOCLING_DOCUMENT_TIMEOUT", "600")
            ),
            "remote_services": False,
            "asr_model": "whisper-small-native",
            "video_frame_sampling": True,
            "speaker_diarization": False,
        },
        "docling_status": status_name,
        "pages": pages,
        "characters": len(canonical_text),
        "paragraphs": len(paragraphs),
        "sentences": sum(len(item.sentences) for item in paragraphs),
        "tables": sum(item.kind == "table" for item in assets),
        "figures": sum(item.kind != "table" for item in assets),
        "canonical_text_uri": canonical_uri,
        "docling_document_uri": document_uri,
        "confidence": _model_dump(getattr(result, "confidence", None)),
        "errors": [_model_dump(item) for item in (getattr(result, "errors", None) or [])],
    }
    receipt_uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=f"{prefix}/normalization-receipt.json",
        payload=receipt_payload,
    )
    source = source.model_copy(update={
        "metadata": {**source.metadata, "normalization_receipt_uri": receipt_uri}
    })
    return DocumentNormalization(
        source=source,
        status=normalized_status,
        pages=pages,
        receipt_uri=receipt_uri,
    )


_CAPTION = re.compile(
    r"^(figure|fig\.?|table|tabela|quadro)\s+\d+(?:[-.]\d+)*(?:\s*[:.\-–]|\s+)",
    re.IGNORECASE,
)


def _normalize_native_pdf(
    data: bytes, *, project_id: UUID, source_id: UUID,
    source_ordinal: int, title: str | None, language: str,
    input_uri: str, original_uri: str, client: storage.Client,
    bucket_name: str, on_page: Callable[[int, int], None] | None,
) -> DocumentNormalization:
    """Extract a PDF's existing text layer; keep page references and the original."""
    from pypdf import PdfReader

    reader = PdfReader(BytesIO(data))
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("encrypted PDF requires a password")
    total_pages = len(reader.pages)
    raw_pages: list[str] = []
    pages_with_text = 0
    for index, page in enumerate(reader.pages, start=1):
        # Layout mode preserves block breaks and caption lines, while still
        # reading the PDF text layer without rendering pages or invoking OCR.
        value = (page.extract_text(extraction_mode="layout") or "") if "/Contents" in page else ""
        raw_pages.append(value)
        pages_with_text += bool(value.strip())
        if on_page and (index == 1 or index % 10 == 0 or index == total_pages):
            on_page(index, total_pages)
    if not pages_with_text:
        raise ValueError("PDF has no native text; OCR is required")
    # A sparse cover or chapter-ending page is valid. Preserve every page,
    # including empty ones, in coverage metadata rather than guessing from length.
    blocks_by_page = pdf_page_blocks(raw_pages)
    prefix = f"hefesto-product/projects/{project_id}/sources/{source_id}"
    blocks: list[str] = []
    paragraphs: list[Paragraph] = []
    assets: list[SourceAsset] = []
    asset_links: list[ParagraphAssetLink] = []
    cursor = 0
    captions: list[dict[str, object]] = []
    for page_no, page_blocks in enumerate(blocks_by_page, start=1):
        for block in page_blocks:
            if blocks:
                cursor += 2
            start = cursor
            cursor += len(block)
            blocks.append(block)
            origin = f"pdf-page-{page_no}-paragraph-{len(paragraphs)+1}"
            caption_match = _CAPTION.match(block)
            is_caption = bool(caption_match)
            paragraph = _paragraph(
                source_id=source_id, source_ordinal=source_ordinal,
                ordinal=len(paragraphs) + 1, text=block,
                element_type="caption" if is_caption else "paragraph",
                page_no=page_no, bbox=None, canonical_start=start,
                canonical_end=cursor, origin_ref=origin,
            )
            paragraphs.append(paragraph)
            if is_caption:
                captions.append({"text": block[:500], "page_no": page_no})
                kind = "table" if caption_match.group(1).lower() in {"table", "tabela", "quadro"} else "figure"
                asset_id = content_hash(f"{source_id}:{origin}:{kind}:{content_hash(block)}")
                assets.append(SourceAsset(
                    asset_id=asset_id, source_id=source_id,
                    source_ordinal=source_ordinal, ordinal=len(assets) + 1,
                    kind=kind, role="content", page_no=page_no,
                    caption=block[:500], text_representation=block,
                    origin_ref=origin, payload={"extraction": "native_pdf_caption"},
                ))
                asset_links.append(ParagraphAssetLink(
                    paragraph_id=paragraph.paragraph_id, asset_id=asset_id,
                    relation="caption", distance=0, primary=True,
                ))
    if not paragraphs:
        raise ValueError("PDF native text contained no usable paragraphs; OCR is required")
    canonical_text = "\n\n".join(blocks)
    canonical_uri = persist_text_immutable(
        client, bucket_name=bucket_name,
        name=f"{prefix}/canonical/content.txt", text=canonical_text,
    )
    metadata: dict[str, object] = {
        "input_uri": input_uri, "original_gcs_uri": original_uri,
        "content_type": "application/pdf", "input_format": "pdf",
        "pages": total_pages, "pages_with_text": pages_with_text,
        "pages_without_text": total_pages - pages_with_text,
        "bytes": len(data), "binary_sha256": sha256(data).hexdigest(),
        "normalized_text_sha256": sha256(canonical_text.encode()).hexdigest(),
        "normalizer": "pypdf-native-text-v1", "canonical_text_uri": canonical_uri,
        "normalization_status": "normalized", "captions": captions,
    }
    source = NormalizedSource(
        source_id=source_id, title=title, language=language,
        text=canonical_text, metadata=metadata, paragraphs=paragraphs,
        assets=assets, asset_links=asset_links,
    )
    receipt_uri = persist_json_immutable(
        client, bucket_name=bucket_name,
        name=f"{prefix}/normalization-receipt.json",
        payload={
            "status": "normalized", "source_id": str(source_id),
            "normalizer": "pypdf-native-text-v1", "pages": total_pages,
            "pages_with_text": pages_with_text,
            "pages_without_text": total_pages - pages_with_text,
            "characters": len(canonical_text), "paragraphs": len(paragraphs),
            "sentences": sum(len(p.sentences) for p in paragraphs),
            "tables": sum(a.kind == "table" for a in assets),
            "figures": sum(a.kind != "table" for a in assets),
            "captions": captions, "canonical_text_uri": canonical_uri,
            "ocr_used": False, "original_gcs_uri": original_uri,
        },
    )
    source = source.model_copy(update={
        "metadata": {**metadata, "normalization_receipt_uri": receipt_uri}
    })
    return DocumentNormalization(
        source=source, status="normalized", pages=total_pages,
        receipt_uri=receipt_uri,
    )


def _paragraph(
    *,
    source_id: UUID,
    source_ordinal: int,
    ordinal: int,
    text: str,
    element_type: str,
    page_no: int | None,
    bbox: BoundingBox | None,
    canonical_start: int,
    canonical_end: int,
    origin_ref: str,
) -> Paragraph:
    paragraph_id = content_hash(
        f"{source_id}:{ordinal}:{content_hash(text)}"
    )
    return Paragraph(
        paragraph_id=paragraph_id,
        source_id=source_id,
        source_ordinal=source_ordinal,
        ordinal=ordinal,
        text=text,
        sentences=_sentences(paragraph_id, text),
        element_type=element_type,
        page_no=page_no,
        bbox=bbox,
        canonical_start=canonical_start,
        canonical_end=canonical_end,
        origin_refs=[origin_ref],
    )


def _asset_links(entries: list[_BodyEntry]) -> list[ParagraphAssetLink]:
    paragraphs = [item for item in entries if item.paragraph_id]
    links: list[ParagraphAssetLink] = []
    for asset in (item for item in entries if item.asset_id):
        previous = [item for item in paragraphs if item.position < asset.position]
        following = [item for item in paragraphs if item.position > asset.position]
        before = previous[-1] if previous else None
        after = following[0] if following else None
        primary = before or after
        if primary is not None:
            links.append(ParagraphAssetLink(
                paragraph_id=str(primary.paragraph_id),
                asset_id=str(asset.asset_id),
                relation="anchor_after" if before else "anchor_before",
                distance=abs(asset.position - primary.position),
                primary=True,
            ))
        if after is not None and after is not primary:
            links.append(ParagraphAssetLink(
                paragraph_id=str(after.paragraph_id),
                asset_id=str(asset.asset_id),
                relation="caption" if after.is_caption else "context_after",
                distance=after.position - asset.position,
            ))
    return links


def _status_name(value: object) -> str:
    raw = getattr(value, "value", value)
    return str(raw).rsplit(".", 1)[-1].lower()


def _conversion_failure(result: Any, status_name: str) -> str:
    errors = getattr(result, "errors", None) or []
    detail = "; ".join(str(getattr(item, "error_message", item)) for item in errors)
    return f"Docling document conversion {status_name}: {detail or 'no usable document'}"


def _label(item: Any) -> str:
    value = getattr(item, "label", "")
    return str(getattr(value, "value", value)).lower()


def _element_type(label: str) -> str:
    aliases = {
        "section_header": "section_header",
        "title": "title",
        "list_item": "list_item",
        "caption": "caption",
        "formula": "formula",
    }
    return aliases.get(label, "paragraph")


def _is_furniture(item: Any) -> bool:
    parent = getattr(item, "parent", None)
    parent_ref = str(getattr(parent, "cref", parent) or "")
    label = _label(item)
    return "furniture" in parent_ref or label in {"page_header", "page_footer"}


def _provenance(item: Any) -> tuple[int | None, BoundingBox | None]:
    values = getattr(item, "prov", None) or []
    if not values:
        return None, None
    value = values[0]
    page_no = getattr(value, "page_no", None)
    box = getattr(value, "bbox", None)
    if box is None:
        return page_no, None
    origin = getattr(box, "coord_origin", None)
    return page_no, BoundingBox(
        left=float(getattr(box, "l", getattr(box, "left", 0.0))),
        top=float(getattr(box, "t", getattr(box, "top", 0.0))),
        right=float(getattr(box, "r", getattr(box, "right", 0.0))),
        bottom=float(getattr(box, "b", getattr(box, "bottom", 0.0))),
        coord_origin=str(getattr(origin, "value", origin) or "") or None,
    )


def _caption(item: Any, document: Any) -> str | None:
    method = getattr(item, "caption_text", None)
    if callable(method):
        try:
            value = str(method(document) or "").strip()
            return value or None
        except (AttributeError, KeyError, TypeError, ValueError):
            return None
    return None


def _is_full_page_ocr_picture(
    item: Any,
    document: Any,
    page_no: int | None,
    bbox: BoundingBox | None,
) -> bool:
    if not (getattr(item, "children", None) or []) or page_no is None or bbox is None:
        return False
    pages = getattr(document, "pages", {}) or {}
    page = pages.get(page_no) if hasattr(pages, "get") else None
    size = getattr(page, "size", None)
    width = float(getattr(size, "width", 0.0) or 0.0)
    height = float(getattr(size, "height", 0.0) or 0.0)
    if width <= 0 or height <= 0:
        return False
    item_area = abs(bbox.right - bbox.left) * abs(bbox.top - bbox.bottom)
    return item_area / (width * height) >= 0.85


def _table_export(item: Any, document: Any, output: str) -> str:
    method = getattr(item, f"export_to_{output}", None)
    if not callable(method):
        return ""
    try:
        return str(method(doc=document) or "").strip()
    except (AttributeError, KeyError, TypeError, ValueError):
        return ""


def _item_image_bytes(item: Any, document: Any) -> bytes | None:
    method = getattr(item, "get_image", None)
    if not callable(method):
        return None
    try:
        image = method(document)
        if image is None:
            return None
        output = BytesIO()
        image.save(output, format="PNG")
        return output.getvalue()
    except (AttributeError, KeyError, TypeError, ValueError, OSError):
        return None


def _persist_item_image(
    item: Any,
    document: Any,
    client: storage.Client,
    bucket_name: str,
    prefix: str,
    asset_id: str,
) -> tuple[str | None, str | None]:
    data = _item_image_bytes(item, document)
    if not data:
        return None, None
    uri = persist_bytes_immutable(
        client,
        bucket_name=bucket_name,
        name=f"{prefix}/assets/{asset_id}.png",
        data=data,
        content_type="image/png",
    )
    return uri, sha256(data).hexdigest()


def _document_payload(document: Any) -> dict[str, Any]:
    exporter = getattr(document, "export_to_dict", None)
    if callable(exporter):
        value = exporter()
    else:
        value = document.model_dump(mode="json")
    if not isinstance(value, dict):
        raise ValueError("Docling document did not export a JSON object")
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _model_dump(value: Any) -> Any:
    if value is None:
        return None
    method = getattr(value, "model_dump", None)
    if callable(method):
        return method(mode="json")
    if isinstance(value, (str, int, float, bool, list, dict)):
        return value
    return str(value)


def _package_version(package: str) -> str:
    try:
        return version(package)
    except PackageNotFoundError:
        return "unknown"

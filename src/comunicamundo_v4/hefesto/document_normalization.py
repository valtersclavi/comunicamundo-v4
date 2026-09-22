from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from hashlib import sha256
from importlib.metadata import PackageNotFoundError, version
from io import BytesIO
import json
import os
from pathlib import Path
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
from .segmentation import _sentences
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
def _pdf_converter() -> Any:
    """Load Docling only in the worker process and reuse its models."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    options = PdfPipelineOptions()
    options.do_ocr = True
    options.ocr_options = TesseractCliOcrOptions(lang=[])
    options.do_table_structure = True
    options.generate_page_images = True
    options.generate_picture_images = True
    options.images_scale = float(os.environ.get("HEFESTO_DOCLING_IMAGE_SCALE", "2.0"))
    options.document_timeout = float(
        os.environ.get("HEFESTO_DOCLING_DOCUMENT_TIMEOUT", "120")
    )
    artifacts = os.environ.get("HEFESTO_DOCLING_ARTIFACTS_PATH", "").strip()
    if artifacts:
        options.artifacts_path = Path(artifacts)
    return DocumentConverter(
        allowed_formats=[InputFormat.PDF],
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)},
    )


def normalize_pdf_document(
    data: bytes,
    *,
    project_id: UUID,
    source_id: UUID,
    source_ordinal: int,
    title: str | None,
    language: str,
    input_uri: str,
    original_uri: str,
    client: storage.Client,
    bucket_name: str,
) -> DocumentNormalization:
    if not data.startswith(b"%PDF"):
        raise ValueError("source is not a PDF")

    from docling.datamodel.base_models import DocumentStream

    result = _pdf_converter().convert(
        DocumentStream(name=f"{source_id}.pdf", stream=BytesIO(data)),
        raises_on_error=False,
        max_file_size=200 * 1024 * 1024,
    )
    status_name = _status_name(result.status)
    if status_name not in {"success", "partial_success"}:
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
            html = _table_export(item, document, "html")
            readable = _table_export(item, document, "markdown")
            caption = _caption(item, document)
            table_payload = _model_dump(item)
            if not isinstance(table_payload, dict):
                table_payload = {"value": table_payload}
            table_identity = json.dumps(
                table_payload, ensure_ascii=False, sort_keys=True, default=str
            )
            asset_id = content_hash(
                f"{source_id}:{origin_ref}:table:{content_hash(table_identity)}"
            )
            structured_uri = persist_json_immutable(
                client,
                bucket_name=bucket_name,
                name=f"{prefix}/assets/{asset_id}.json",
                payload=table_payload,
            )
            html_uri = None
            if html:
                html_uri = persist_bytes_immutable(
                    client,
                    bucket_name=bucket_name,
                    name=f"{prefix}/assets/{asset_id}.html",
                    data=html.encode("utf-8"),
                    content_type="text/html; charset=utf-8",
                )
            blob_uri, binary_digest = _persist_item_image(
                item, document, client, bucket_name, prefix, asset_id
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
                blob_uri=blob_uri,
                structured_uri=structured_uri,
                binary_sha256=binary_digest,
                origin_ref=origin_ref,
                payload={"docling_label": label, "html_uri": html_uri},
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
            image_data = _item_image_bytes(item, document)
            image_digest = sha256(image_data).hexdigest() if image_data else None
            asset_id = content_hash(
                f"{source_id}:{origin_ref}:picture:{image_digest or content_hash(caption or origin_ref)}"
            )
            blob_uri = None
            if image_data:
                blob_uri = persist_bytes_immutable(
                    client,
                    bucket_name=bucket_name,
                    name=f"{prefix}/assets/{asset_id}.png",
                    data=image_data,
                    content_type="image/png",
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
                blob_uri=blob_uri,
                binary_sha256=image_digest,
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
        raise ValueError("PDF contains no usable text after structured conversion")

    canonical_text = "\n\n".join(canonical_blocks).strip()
    asset_links = _asset_links(entries)
    canonical_uri = persist_text_immutable(
        client,
        bucket_name=bucket_name,
        name=f"{prefix}/canonical/content.txt",
        text=canonical_text,
    )
    document_payload = _document_payload(document)
    document_uri = persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=f"{prefix}/canonical/document.json",
        payload=document_payload,
    )
    pages = len(getattr(document, "pages", {}) or {})
    normalized_status = "partial" if status_name == "partial_success" else "normalized"
    metadata: dict[str, object] = {
        "input_uri": input_uri,
        "original_gcs_uri": original_uri,
        "content_type": "application/pdf",
        "pages": pages,
        "bytes": len(data),
        "binary_sha256": sha256(data).hexdigest(),
        "normalized_text_sha256": sha256(canonical_text.encode()).hexdigest(),
        "normalizer": "docling-pdf-v1",
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
        "normalizer": "docling-pdf-v1",
        "docling_version": _package_version("docling"),
        "docling_core_version": _package_version("docling-core"),
        "configuration": {
            "ocr_engine": "tesseract-cli",
            "ocr_language": "automatic-script-detection",
            "table_structure": True,
            "picture_images": True,
            "image_scale": float(os.environ.get("HEFESTO_DOCLING_IMAGE_SCALE", "2.0")),
            "document_timeout_seconds": float(
                os.environ.get("HEFESTO_DOCLING_DOCUMENT_TIMEOUT", "120")
            ),
            "remote_services": False,
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
    return f"Docling PDF conversion {status_name}: {detail or 'no usable document'}"


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

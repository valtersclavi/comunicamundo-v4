from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse


MAX_DOCUMENT_BYTES = 200 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DocumentFormat:
    extension: str
    content_type: str
    family: str
    label: str


_FORMATS = (
    DocumentFormat(".pdf", "application/pdf", "document", "PDF"),
    DocumentFormat(".docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document", "document", "Word"),
    DocumentFormat(".pptx", "application/vnd.openxmlformats-officedocument.presentationml.presentation", "document", "PowerPoint"),
    DocumentFormat(".xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", "document", "Excel"),
    DocumentFormat(".html", "text/html", "document", "HTML"),
    DocumentFormat(".htm", "text/html", "document", "HTML"),
    DocumentFormat(".xhtml", "application/xhtml+xml", "document", "XHTML"),
    DocumentFormat(".epub", "application/epub+zip", "document", "EPUB"),
    DocumentFormat(".md", "text/markdown", "document", "Markdown"),
    DocumentFormat(".txt", "text/plain", "text", "Texto"),
    DocumentFormat(".png", "image/png", "image", "Imagem"),
    DocumentFormat(".jpg", "image/jpeg", "image", "Imagem"),
    DocumentFormat(".jpeg", "image/jpeg", "image", "Imagem"),
    DocumentFormat(".tif", "image/tiff", "image", "Imagem"),
    DocumentFormat(".tiff", "image/tiff", "image", "Imagem"),
    DocumentFormat(".webp", "image/webp", "image", "Imagem"),
    DocumentFormat(".wav", "audio/wav", "audio", "Áudio"),
    DocumentFormat(".mp3", "audio/mpeg", "audio", "Áudio"),
    DocumentFormat(".aac", "audio/aac", "audio", "Áudio"),
    DocumentFormat(".mp4", "video/mp4", "video", "Vídeo"),
    DocumentFormat(".avi", "video/x-msvideo", "video", "Vídeo"),
    DocumentFormat(".mov", "video/quicktime", "video", "Vídeo"),
    DocumentFormat(".webm", "video/webm", "video", "Vídeo"),
)

FORMATS_BY_EXTENSION = {item.extension: item for item in _FORMATS}
SUPPORTED_EXTENSIONS = tuple(FORMATS_BY_EXTENSION)
SUPPORTED_CONTENT_TYPES = frozenset(item.content_type for item in _FORMATS)
UPLOAD_ACCEPT = ",".join(SUPPORTED_EXTENSIONS)
_PREFERRED_EXTENSION = {
    "text/html": ".html",
    "text/markdown": ".md",
    "image/jpeg": ".jpg",
    "image/tiff": ".tiff",
}


def source_file_name(
    uri: str,
    title: str | None = None,
    content_type: str | None = None,
) -> str:
    for candidate in (title, Path(urlparse(uri).path).name):
        value = Path(candidate or "").name.strip()
        if Path(value).suffix.lower() in FORMATS_BY_EXTENSION:
            return value
    declared = (content_type or "").split(";", 1)[0].strip().lower()
    matching = [item for item in _FORMATS if item.content_type == declared]
    if len(matching) == 1:
        return f"source{matching[0].extension}"
    if declared in _PREFERRED_EXTENSION:
        return f"source{_PREFERRED_EXTENSION[declared]}"
    if declared == "application/pdf":
        return "source.pdf"
    return "source.bin"


def resolve_document_format(
    file_name: str,
    declared_content_type: str | None = None,
) -> DocumentFormat:
    extension = Path(file_name).suffix.lower()
    value = FORMATS_BY_EXTENSION.get(extension)
    if value is None:
        supported = ", ".join(SUPPORTED_EXTENSIONS)
        raise ValueError(f"unsupported source format; accepted extensions: {supported}")
    declared = (declared_content_type or "").split(";", 1)[0].strip().lower()
    if declared and declared not in {
        value.content_type,
        "application/octet-stream",
        "binary/octet-stream",
        # Browsers disagree on these common media aliases.
        "audio/x-wav" if extension == ".wav" else value.content_type,
        "audio/mp3" if extension == ".mp3" else value.content_type,
        "video/avi" if extension == ".avi" else value.content_type,
    }:
        raise ValueError(
            f"declared content type {declared!r} does not match {extension}"
        )
    return value


def is_docling_format(value: DocumentFormat) -> bool:
    return value.family != "text"

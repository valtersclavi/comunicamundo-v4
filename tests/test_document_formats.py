from __future__ import annotations

from pathlib import Path
import re

import pytest

from comunicamundo_v8.hefesto.api import UploadRequest
from comunicamundo_v8.hefesto.document_formats import (
    FORMATS_BY_EXTENSION,
    resolve_document_format,
    source_file_name,
)


@pytest.mark.parametrize(
    "expected",
    FORMATS_BY_EXTENSION.values(),
    ids=lambda item: item.extension,
)
def test_every_input_format_has_an_upload_contract(expected) -> None:
    file_name = f"fonte{expected.extension}"
    upload = UploadRequest(
        file_name=file_name,
        content_type=expected.content_type,
        size_bytes=10,
    )
    assert upload.content_type == expected.content_type
    assert resolve_document_format(file_name, upload.content_type) == expected


def test_extension_and_declared_type_must_agree() -> None:
    with pytest.raises(ValueError, match="does not match"):
        UploadRequest(
            file_name="disfarce.pdf",
            content_type="video/mp4",
            size_bytes=10,
        )


def test_v8_upload_formats_match_document_readers() -> None:
    expected = {
        ".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".xhtml",
        ".epub", ".md", ".txt", ".jpg", ".jpeg", ".png", ".tif",
        ".tiff", ".webp", ".aac", ".mp3", ".wav", ".avi", ".mov",
        ".mp4", ".webm",
    }
    assert set(FORMATS_BY_EXTENSION) == expected

    for extension in (".bmp", ".markdown", ".m4a", ".ogg", ".flac", ".mkv"):
        with pytest.raises(ValueError, match="unsupported source format"):
            resolve_document_format(f"fonte{extension}")
        with pytest.raises(ValueError, match="unsupported source format"):
            UploadRequest(file_name=f"fonte{extension}", content_type="application/octet-stream", size_bytes=10)


def test_url_without_extension_uses_unambiguous_content_type() -> None:
    assert source_file_name(
        "https://example.com/download/123",
        "Relatório",
        "application/pdf",
    ) == "source.pdf"

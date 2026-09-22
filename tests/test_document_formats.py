from __future__ import annotations

import pytest

from comunicamundo_v4.hefesto.api import UploadRequest
from comunicamundo_v4.hefesto.document_formats import (
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


def test_url_without_extension_uses_unambiguous_content_type() -> None:
    assert source_file_name(
        "https://example.com/download/123",
        "Relatório",
        "application/pdf",
    ) == "source.pdf"

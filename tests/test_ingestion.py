from __future__ import annotations

from base64 import b64encode
import json
from uuid import uuid4

import pytest

from comunicamundo_v4.hefesto import ingestion
from comunicamundo_v4.hefesto.contracts import EditorialInputRequest
from comunicamundo_v4.hefesto.ingestion import extract_pdf, ingest, normalize_pdf_pages
from comunicamundo_v4.hefesto.storage import load_input_request
from comunicamundo_v4.hefesto.worker import parser


class Blob:
    def __init__(self, values: dict[str, bytes], name: str) -> None:
        self.values = values
        self.name = name

    def upload_from_string(self, data, **kwargs) -> None:
        if kwargs.get("if_generation_match") == 0 and self.name in self.values:
            from google.api_core.exceptions import PreconditionFailed
            raise PreconditionFailed("exists")
        self.values[self.name] = data.encode() if isinstance(data, str) else data

    def download_as_bytes(self) -> bytes:
        return self.values[self.name]


class Bucket:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def blob(self, name: str) -> Blob:
        return Blob(self.values, name)


class Storage:
    def __init__(self) -> None:
        self.buckets: dict[str, dict[str, bytes]] = {}

    def bucket(self, name: str) -> Bucket:
        return Bucket(self.buckets.setdefault(name, {}))


class Reader:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values
        self.calls: list[str] = []

    def read(self, uri: str) -> bytes:
        self.calls.append(uri)
        return self.values[uri]


def text_pdf() -> bytes:
    content = (
        b"BT /F1 12 Tf 30 250 Td (Primeiro paragrafo.) Tj "
        b"0 -20 Td (Continua na mesma ideia.) Tj "
        b"0 -50 Td (Segundo paragrafo.) Tj ET"
    )
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 300] "
            b"/Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>"
        ),
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Length " + str(len(content)).encode() + b" >>\nstream\n"
        + content + b"\nendstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = []
    for ordinal, value in enumerate(objects, start=1):
        offsets.append(len(data))
        data.extend(f"{ordinal} 0 obj\n".encode() + value + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects) + 1}\n".encode())
    data.extend(b"0000000000 65535 f \n")
    for offset in offsets:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
        f"startxref\n{xref}\n%%EOF\n".encode()
    )
    return bytes(data)


def input_request() -> EditorialInputRequest:
    return EditorialInputRequest.model_validate({
        "project_id": str(uuid4()),
        "brief": {
            "theme": "Tema genérico",
            "approach": "Intenção do usuário",
            "duration_minutes": 12,
            "output_language": "pt-BR",
            "audience": "Público definido",
            "product": "article",
        },
        "sources": [
            {"uri": "gs://uploads/one.pdf", "title": "One", "language": "en"},
            {"uri": "https://example.com/two.pdf", "title": "Two", "language": "pt-BR"},
        ],
    })


def test_generic_ingestion_normalizes_each_pdf_once_before_any_model(monkeypatch) -> None:
    source = input_request()
    storage = Storage()
    reader = Reader({item.uri: b"%PDF synthetic" for item in source.sources})
    calls: list[bytes] = []

    def fake_extract(data: bytes) -> tuple[str, int]:
        calls.append(data)
        return "Primeira frase. Continuação.\n\nSegundo parágrafo.", 3

    monkeypatch.setattr(ingestion, "extract_pdf", fake_extract)
    first = ingest(source, client=storage, bucket_name="product", reader=reader)
    second = ingest(source, client=storage, bucket_name="product", reader=reader)

    assert first.request == second.request
    assert first.receipt.model_invocations == 0
    assert first.receipt.source_count == 2
    assert first.receipt.paragraph_count == 4
    assert first.receipt.sentence_count == 6
    assert len(calls) == 4
    assert reader.calls == [item.uri for item in source.sources] * 2
    assert all(item.original_uri.startswith("gs://product/") for item in first.receipt.sources)
    assert first.receipt.normalized_request_uri.endswith("/normalized-request.json")
    assert first.receipt_uri.endswith("/ingestion-receipt.json")


def test_pdf_normalization_uses_layout_blocks_not_character_limits() -> None:
    long_line = "conteúdo " * 10_000
    text = normalize_pdf_pages([
        "RELATÓRIO\n\nUma ideia que conti-\nnua na linha seguinte.\n\n" + long_line + "\nRODAPÉ",
        "RELATÓRIO\n\nOutro parágrafo.\nRODAPÉ",
        "RELATÓRIO\n\nTerceiro parágrafo.\nRODAPÉ",
    ])
    paragraphs = text.split("\n\n")
    assert "continua" in paragraphs[0]
    assert long_line.strip() in paragraphs
    assert "RELATÓRIO" not in text
    assert "RODAPÉ" not in text
    assert len(paragraphs) == 4


def test_real_text_pdf_is_extracted_into_semantic_layout_blocks() -> None:
    text, pages = extract_pdf(text_pdf())
    assert pages == 1
    assert text.split("\n\n") == [
        "Primeiro paragrafo. Continua na mesma ideia.",
        "Segundo paragrafo.",
    ]


def test_input_manifest_can_travel_as_small_base64_json() -> None:
    source = input_request()
    encoded = b64encode(source.model_dump_json().encode()).decode()
    loaded = load_input_request(Storage(), encoded=encoded)
    assert loaded == source


def test_remote_sources_reject_private_networks(monkeypatch) -> None:
    monkeypatch.setattr(
        ingestion.socket,
        "getaddrinfo",
        lambda *args: [(None, None, None, None, ("169.254.169.254", 443))],
    )
    with pytest.raises(ValueError, match="public addresses"):
        ingestion._validate_public_https("https://metadata.example/source.pdf")


def test_non_pdf_fails_before_model_work() -> None:
    with pytest.raises(ValueError, match="not a PDF"):
        ingestion.extract_pdf(b"not a pdf")


def test_cli_is_product_generic_and_has_no_test_variant() -> None:
    root = parser()
    assert root.parse_args(["plan", "--request-base64", "e30="]).command == "plan"
    with pytest.raises(SystemExit):
        root.parse_args(["run-proof", "--variant", "A"])
    with pytest.raises(SystemExit):
        root.parse_args(["digest-normalized", "--request", "gs://legacy/request.json"])

from comunicamundo_v4.hefesto.proof_requests import _extract_text, _source_definitions, stage_proof_request


class Blob:
    def __init__(self) -> None:
        self.value = None

    def upload_from_string(self, value, **kwargs):
        assert kwargs["if_generation_match"] == 0
        self.value = value


class Bucket:
    def __init__(self) -> None:
        self.blobs = {}

    def blob(self, name):
        return self.blobs.setdefault(name, Blob())


class Storage:
    def __init__(self) -> None:
        self.stored = Bucket()

    def bucket(self, name):
        assert name == "proof-bucket"
        return self.stored


def test_canary_is_staged_by_runtime_with_original_and_request() -> None:
    storage = Storage()
    staged = stage_proof_request("canary", bucket_name="proof-bucket", storage_client=storage)

    assert staged.proof == "postgres-canary"
    assert staged.variant == "canary"
    assert len(staged.sources) == 1
    assert staged.sources[0].pages == 1
    assert staged.sources[0].original_gcs_uri in {
        f"gs://proof-bucket/{name}" for name in storage.stored.blobs
    }
    assert staged.request_uri in {f"gs://proof-bucket/{name}" for name in storage.stored.blobs}
    assert len(storage.stored.blobs) == 3


def test_ab_definitions_share_exactly_three_sources() -> None:
    a, b = _source_definitions("A"), _source_definitions("B")

    assert a[:3] == b[:3]
    assert a[3] != b[3]
    assert "Highlights" in a[3][0]
    assert "Full Report" in b[3][0]
    assert a[0][1] == "https://www.nature.com/articles/s41562-025-02134-4.pdf"


def test_text_extraction_normalizes_without_semantic_truncation() -> None:
    text, pages = _extract_text(b"Primeira linha.  \r\n\r\nSegunda linha.", extension="txt")

    assert pages == 1
    assert text == "Primeira linha.\n\nSegunda linha."

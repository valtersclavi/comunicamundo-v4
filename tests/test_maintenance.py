from uuid import UUID

import pytest

from comunicamundo_v4.hefesto.maintenance import (
    _canonical_ids,
    _input_uris,
    storage_partition,
)


A = "f88c65b4-414b-56d1-9820-19489159922c"
B = "d9a77477-e5aa-5dbc-ad1a-e8434a84e98e"


def test_storage_partition_preserves_only_exact_test_assets() -> None:
    bucket = "hefesto-bucket"
    preserved, removable = storage_partition(
        [
            f"hefesto-product/executions/{A}/receipt.json",
            f"hefesto-product/executions/{A}-old/receipt.json",
            "hefesto-product/projects/project-a/normalized-request.json",
            "hefesto-product/projects/project-old/normalized-request.json",
            "hefesto-product/users/user/uploads/a.pdf",
            "hefesto-product/users/user/uploads/old.pdf",
        ],
        bucket_name=bucket,
        execution_ids=[A, B],
        project_ids=["project-a", "project-b"],
        input_uris=["gs://hefesto-bucket/hefesto-product/users/user/uploads/a.pdf"],
    )

    assert preserved == [
        f"hefesto-product/executions/{A}/receipt.json",
        "hefesto-product/projects/project-a/normalized-request.json",
        "hefesto-product/users/user/uploads/a.pdf",
    ]
    assert removable == [
        f"hefesto-product/executions/{A}-old/receipt.json",
        "hefesto-product/projects/project-old/normalized-request.json",
        "hefesto-product/users/user/uploads/old.pdf",
    ]


def test_keep_ids_are_unique_sorted_and_mandatory() -> None:
    assert _canonical_ids([UUID(A), UUID(B), UUID(A)]) == tuple(sorted([A, B]))
    with pytest.raises(ValueError, match="at least one"):
        _canonical_ids([])


def test_preserved_submission_requires_every_source_uri() -> None:
    assert _input_uris({"sources": [{"uri": "https://example.test/a.pdf"}]}) == {
        "https://example.test/a.pdf"
    }
    with pytest.raises(ValueError, match="invalid source URI"):
        _input_uris({"sources": [{"title": "missing"}]})

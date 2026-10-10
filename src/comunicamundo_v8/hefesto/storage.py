from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from google.cloud import storage

from google.api_core.exceptions import PreconditionFailed

from .contracts import EditorialInputRequest, PlanningRequest


def parse_gs_uri(uri: str) -> tuple[str, str]:
    if not uri.startswith("gs://"):
        raise ValueError("expected gs:// URI")
    bucket, separator, name = uri[5:].partition("/")
    if not bucket or not separator or not name:
        raise ValueError("invalid gs:// URI")
    return bucket, name




def load_input_request(
    client: storage.Client,
    *,
    uri: str,
) -> EditorialInputRequest:
    bucket, name = parse_gs_uri(uri)
    data = client.bucket(bucket).blob(name).download_as_bytes()
    return EditorialInputRequest.model_validate_json(data)


def persist_json_immutable(
    client: storage.Client,
    *,
    bucket_name: str,
    name: str,
    payload: dict[str, Any],
) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    persist_bytes_immutable(
        client, bucket_name=bucket_name, name=name, data=data,
        content_type="application/json",
    )
    return f"gs://{bucket_name}/{name}"


def persist_text_immutable(
    client: storage.Client,
    *,
    bucket_name: str,
    name: str,
    text: str,
) -> str:
    persist_bytes_immutable(
        client,
        bucket_name=bucket_name,
        name=name,
        data=text.encode("utf-8"),
        content_type="text/plain; charset=utf-8",
    )
    return f"gs://{bucket_name}/{name}"


def persist_bytes_immutable(
    client: storage.Client,
    *,
    bucket_name: str,
    name: str,
    data: bytes,
    content_type: str,
) -> str:
    blob = client.bucket(bucket_name).blob(name)
    try:
        blob.upload_from_string(
            data, content_type=content_type, if_generation_match=0
        )
    except PreconditionFailed:
        if blob.download_as_bytes() != data:
            raise ValueError(f"immutable object conflict: {name}")
    return f"gs://{bucket_name}/{name}"




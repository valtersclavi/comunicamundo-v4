from __future__ import annotations

import json
from base64 import b64decode
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


def load_request(client: storage.Client, uri: str) -> PlanningRequest:
    bucket, name = parse_gs_uri(uri)
    data = client.bucket(bucket).blob(name).download_as_bytes()
    return PlanningRequest.model_validate_json(data)


def load_input_request(
    client: storage.Client,
    *,
    uri: str | None = None,
    encoded: str | None = None,
) -> EditorialInputRequest:
    if (uri is None) == (encoded is None):
        raise ValueError("provide exactly one input request transport")
    if uri is not None:
        bucket, name = parse_gs_uri(uri)
        data = client.bucket(bucket).blob(name).download_as_bytes()
    else:
        try:
            data = b64decode(encoded or "", validate=True)
        except ValueError as error:
            raise ValueError("input request is not valid base64") from error
    if len(data) > 1_000_000:
        raise ValueError("input request exceeds 1 MB")
    return EditorialInputRequest.model_validate_json(data)


def persist_json_immutable(
    client: storage.Client,
    *,
    bucket_name: str,
    name: str,
    payload: dict[str, Any],
) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    blob = client.bucket(bucket_name).blob(name)
    persist_bytes_immutable(
        client, bucket_name=bucket_name, name=name, data=data,
        content_type="application/json",
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


def persist_receipt(
    client: storage.Client,
    *,
    bucket_name: str,
    execution_run_id: UUID,
    payload: dict[str, Any],
) -> str:
    name = f"hefesto-product/executions/{execution_run_id}/receipt.json"
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    client.bucket(bucket_name).blob(name).upload_from_string(
        data, content_type="application/json"
    )
    return f"gs://{bucket_name}/{name}"

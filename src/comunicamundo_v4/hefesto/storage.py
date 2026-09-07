from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from google.cloud import storage

from .contracts import PlanningRequest


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


def persist_json_immutable(
    client: storage.Client,
    *,
    bucket_name: str,
    name: str,
    payload: dict[str, Any],
) -> str:
    data = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode()
    blob = client.bucket(bucket_name).blob(name)
    blob.upload_from_string(data, content_type="application/json", if_generation_match=0)
    return f"gs://{bucket_name}/{name}"


def persist_receipt(
    client: storage.Client,
    *,
    bucket_name: str,
    execution_run_id: UUID,
    payload: dict[str, Any],
) -> str:
    return persist_json_immutable(
        client,
        bucket_name=bucket_name,
        name=f"hefesto-product/executions/{execution_run_id}/receipt.json",
        payload=payload,
    )


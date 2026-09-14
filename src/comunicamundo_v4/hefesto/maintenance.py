from __future__ import annotations

from collections.abc import Iterable
from typing import Any
from uuid import UUID

from google.api_core.exceptions import NotFound
from google.cloud import storage
import psycopg

from .storage import parse_gs_uri


PRODUCT_PREFIX = "hefesto-product/"
_EXECUTION_TABLES = (
    "sentences",
    "selection_batches",
    "editorial_condensations",
    "paragraphs",
    "call_usage",
    "stage_receipts",
    "runs",
    "submissions",
)


def _canonical_ids(execution_ids: Iterable[UUID]) -> tuple[str, ...]:
    values = tuple(sorted({str(value) for value in execution_ids}))
    if not values:
        raise ValueError("at least one preserved execution is required")
    return values


def _input_uris(request_payload: object) -> set[str]:
    if not isinstance(request_payload, dict):
        raise ValueError("preserved submission has no request payload")
    sources = request_payload.get("sources")
    if not isinstance(sources, list):
        raise ValueError("preserved submission has no source list")
    uris = {
        source.get("uri", "").strip()
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("uri"), str)
    }
    if len(uris) != len(sources) or not all(uris):
        raise ValueError("preserved submission contains an invalid source URI")
    return uris


def storage_partition(
    object_names: Iterable[str],
    *,
    bucket_name: str,
    execution_ids: Iterable[str],
    project_ids: Iterable[str],
    input_uris: Iterable[str],
) -> tuple[list[str], list[str]]:
    prefixes = tuple(
        [
            *(f"{PRODUCT_PREFIX}executions/{value}/" for value in execution_ids),
            *(f"{PRODUCT_PREFIX}projects/{value}/" for value in project_ids),
        ]
    )
    exact: set[str] = set()
    for uri in input_uris:
        if not uri.startswith("gs://"):
            continue
        uri_bucket, name = parse_gs_uri(uri)
        if uri_bucket == bucket_name:
            exact.add(name)
    preserved: list[str] = []
    removable: list[str] = []
    for name in sorted(object_names):
        if name in exact or name.startswith(prefixes):
            preserved.append(name)
        else:
            removable.append(name)
    return preserved, removable


def _database_inventory(cursor: Any, keep_ids: tuple[str, ...], upload_uris: tuple[str, ...]) -> dict[str, int]:
    inventory: dict[str, int] = {}
    for table in _EXECUTION_TABLES:
        cursor.execute(
            f"""
            SELECT count(*)
            FROM hefesto_product.{table}
            WHERE NOT (execution_run_id = ANY(%s::uuid[]))
            """,
            (list(keep_ids),),
        )
        inventory[table] = int(cursor.fetchone()[0])
    if upload_uris:
        cursor.execute(
            """
            SELECT count(*)
            FROM hefesto_product.uploads
            WHERE NOT (object_uri = ANY(%s::text[]))
            """,
            (list(upload_uris),),
        )
    else:
        cursor.execute("SELECT count(*) FROM hefesto_product.uploads")
    inventory["uploads"] = int(cursor.fetchone()[0])
    cursor.execute(
        "SELECT count(*) FROM hefesto_product.app_sessions WHERE expires_at < now()"
    )
    inventory["expired_app_sessions"] = int(cursor.fetchone()[0])
    return inventory


def _delete_database_rows(
    cursor: Any, keep_ids: tuple[str, ...], upload_uris: tuple[str, ...]
) -> dict[str, int]:
    deleted: dict[str, int] = {}
    for table in _EXECUTION_TABLES:
        cursor.execute(
            f"""
            DELETE FROM hefesto_product.{table}
            WHERE NOT (execution_run_id = ANY(%s::uuid[]))
            """,
            (list(keep_ids),),
        )
        deleted[table] = cursor.rowcount
    if upload_uris:
        cursor.execute(
            """
            DELETE FROM hefesto_product.uploads
            WHERE NOT (object_uri = ANY(%s::text[]))
            """,
            (list(upload_uris),),
        )
    else:
        cursor.execute("DELETE FROM hefesto_product.uploads")
    deleted["uploads"] = cursor.rowcount
    cursor.execute(
        "DELETE FROM hefesto_product.app_sessions WHERE expires_at < now()"
    )
    deleted["expired_app_sessions"] = cursor.rowcount
    return deleted


def clean_operational_history(
    *,
    direct_url: str,
    client: storage.Client,
    bucket_name: str,
    keep_execution_ids: Iterable[UUID],
    execute: bool = False,
) -> dict[str, object]:
    keep_ids = _canonical_ids(keep_execution_ids)
    with psycopg.connect(direct_url) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    r.execution_run_id::text,
                    r.project_id::text,
                    r.state,
                    r.payload #>> '{final_text,proposal,body}',
                    s.request_payload
                FROM hefesto_product.runs AS r
                LEFT JOIN hefesto_product.submissions AS s
                    USING (execution_run_id)
                WHERE r.execution_run_id = ANY(%s::uuid[])
                """,
                (list(keep_ids),),
            )
            rows = cursor.fetchall()
            found_ids = {row[0] for row in rows}
            missing = sorted(set(keep_ids) - found_ids)
            if missing:
                raise ValueError(
                    "preserved executions not found: " + ", ".join(missing)
                )
            invalid = sorted(
                row[0] for row in rows
                if row[2] != "text_ready" or not str(row[3] or "").strip()
            )
            if invalid:
                raise ValueError(
                    "preserved executions are not complete: " + ", ".join(invalid)
                )

            project_ids = tuple(sorted({row[1] for row in rows}))
            input_uris = set()
            for row in rows:
                input_uris.update(_input_uris(row[4]))
            upload_uris = tuple(sorted(
                uri for uri in input_uris if uri.startswith("gs://")
            ))

            blobs = list(client.list_blobs(bucket_name, prefix=PRODUCT_PREFIX))
            preserved_names, removable_names = storage_partition(
                (blob.name for blob in blobs),
                bucket_name=bucket_name,
                execution_ids=keep_ids,
                project_ids=project_ids,
                input_uris=input_uris,
            )
            inventory = _database_inventory(cursor, keep_ids, upload_uris)

            report: dict[str, object] = {
                "status": "executed" if execute else "dry_run",
                "preserved_execution_run_ids": list(keep_ids),
                "preserved_project_ids": list(project_ids),
                "preserved_input_uris": sorted(input_uris),
                "database_rows_to_delete": inventory,
                "storage": {
                    "prefix": PRODUCT_PREFIX,
                    "objects_scanned": len(blobs),
                    "objects_preserved": len(preserved_names),
                    "objects_to_delete": len(removable_names),
                },
            }
            if not execute:
                return report

            removable = {name for name in removable_names}
            deleted_objects = 0
            for blob in blobs:
                if blob.name not in removable:
                    continue
                try:
                    blob.delete(if_generation_match=blob.generation)
                    deleted_objects += 1
                except NotFound:
                    pass

            deleted_rows = _delete_database_rows(cursor, keep_ids, upload_uris)
            report["database_rows_deleted"] = deleted_rows
            report["storage"]["objects_deleted"] = deleted_objects
            return report

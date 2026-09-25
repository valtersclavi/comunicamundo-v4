from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from .contracts import (
    CallUsage,
    EditorialCondensation,
    Paragraph,
    ParagraphAssetLink,
    ProductRun,
    SelectionBatch,
    SourceAsset,
    EditorialInputRequest,
)


def apply_migrations(direct_url: str, directory: str = "/app/migrations") -> list[str]:
    root = Path(directory)
    files = sorted(root.glob("*.sql"))
    if not files:
        raise ValueError("no product migrations found")
    applied: list[str] = []
    with psycopg.connect(direct_url, autocommit=True) as connection:
        connection.execute("CREATE SCHEMA IF NOT EXISTS hefesto_product")
        connection.execute(
            """CREATE TABLE IF NOT EXISTS hefesto_product.schema_migrations (
               version text PRIMARY KEY, checksum char(64) NOT NULL,
               applied_at timestamptz NOT NULL DEFAULT now())"""
        )
        for path in files:
            sql = path.read_text(encoding="utf-8")
            digest = sha256(sql.encode()).hexdigest()
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT checksum FROM hefesto_product.schema_migrations WHERE version=%s",
                    (path.name,),
                )
                existing = cursor.fetchone()
                if existing and existing[0] != digest:
                    raise ValueError(f"migration checksum changed: {path.name}")
                if not existing:
                    with connection.transaction():
                        cursor.execute(sql)
                        cursor.execute(
                            "INSERT INTO hefesto_product.schema_migrations(version, checksum) VALUES (%s,%s)",
                            (path.name, digest),
                        )
                    applied.append(path.name)
    return applied


class ProductRepository:
    def __init__(self, url: str, *, max_size: int = 8) -> None:
        self._pool = AsyncConnectionPool(url, min_size=0, max_size=max_size, open=False)

    async def open(self) -> None:
        await self._pool.open()

    async def close(self) -> None:
        await self._pool.close()

    async def check(self) -> dict[str, object]:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.execute("SELECT current_database(), 1")
                database, value = await cursor.fetchone()
        return {"database": database, "ready": value == 1}

    async def auth_credential(self, username: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                """SELECT username,password_salt,password_hash,failed_attempts,locked_until
                   FROM hefesto_product.app_credential WHERE username=%s""",
                (username,),
            )
            value = await row.fetchone()
        if not value:
            return None
        return {
            "username": value[0], "salt": bytes(value[1]), "password_hash": bytes(value[2]),
            "failed_attempts": value[3], "locked_until": value[4],
        }

    async def record_auth_result(self, username: str, success: bool) -> None:
        async with self._pool.connection() as connection:
            if success:
                await connection.execute(
                    """UPDATE hefesto_product.app_credential
                       SET failed_attempts=0,locked_until=NULL,updated_at=now()
                       WHERE username=%s""",
                    (username,),
                )
            else:
                await connection.execute(
                    """UPDATE hefesto_product.app_credential SET
                       failed_attempts=failed_attempts+1,
                       locked_until=CASE WHEN failed_attempts+1 >= 10
                         THEN now()+interval '15 minutes' ELSE locked_until END,
                       updated_at=now() WHERE username=%s""",
                    (username,),
                )

    async def create_session(self, owner_id: str, token_hash: str, expires_at: Any) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.app_sessions(token_hash,owner_id,expires_at)
                   VALUES (%s,%s,%s)""",
                (token_hash, owner_id, expires_at),
            )

    async def session_owner(self, token_hash: str) -> str | None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "DELETE FROM hefesto_product.app_sessions WHERE expires_at <= now()"
            )
            row = await connection.execute(
                """SELECT owner_id FROM hefesto_product.app_sessions
                   WHERE token_hash=%s AND expires_at > now()""",
                (token_hash,),
            )
            value = await row.fetchone()
        return str(value[0]) if value else None

    async def delete_session(self, token_hash: str) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                "DELETE FROM hefesto_product.app_sessions WHERE token_hash=%s",
                (token_hash,),
            )

    async def register_submission(
        self,
        execution_run_id: UUID,
        project_id: UUID,
        owner_id: str,
        request_payload: dict[str, Any],
    ) -> None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                """INSERT INTO hefesto_product.submissions
                   (execution_run_id,project_id,owner_id,request_payload)
                   VALUES (%s,%s,%s,%s::jsonb)
                   ON CONFLICT(execution_run_id) DO UPDATE SET updated_at=now()
                   RETURNING owner_id""",
                (execution_run_id, project_id, owner_id, json.dumps(request_payload)),
            )
            stored_owner = (await row.fetchone())[0]
            if stored_owner != owner_id:
                raise PermissionError("execution belongs to another user")

    async def submission_owner(self, execution_run_id: UUID) -> str | None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                "SELECT owner_id FROM hefesto_product.submissions WHERE execution_run_id=%s",
                (execution_run_id,),
            )
            value = await row.fetchone()
        return str(value[0]) if value else None

    async def list_submissions(self, owner_id: str) -> list[dict[str, Any]]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT execution_run_id,project_id,request_payload,created_at,updated_at
                   FROM hefesto_product.submissions WHERE owner_id=%s
                   ORDER BY created_at DESC LIMIT 100""",
                (owner_id,),
            )
            values = await rows.fetchall()
        return [
            {
                "execution_run_id": str(item[0]),
                "project_id": str(item[1]),
                "request": item[2],
                "created_at": item[3].isoformat(),
                "updated_at": item[4].isoformat(),
            }
            for item in values
        ]

    async def record_product_receipt(
        self, execution_run_id: UUID, payload: dict[str, object]
    ) -> None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                """UPDATE hefesto_product.submissions
                   SET receipt_payload=%s::jsonb,updated_at=now()
                   WHERE execution_run_id=%s RETURNING execution_run_id""",
                (json.dumps(payload), execution_run_id),
            )
            if await row.fetchone() is None:
                raise KeyError("unknown execution")

    async def product_receipt(
        self, execution_run_id: UUID
    ) -> dict[str, object] | None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                """SELECT receipt_payload FROM hefesto_product.submissions
                   WHERE execution_run_id=%s""",
                (execution_run_id,),
            )
            value = await row.fetchone()
        return dict(value[0]) if value and value[0] is not None else None

    async def submission_request(
        self, execution_run_id: UUID
    ) -> EditorialInputRequest | None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                "SELECT request_payload FROM hefesto_product.submissions WHERE execution_run_id=%s",
                (execution_run_id,),
            )
            value = await row.fetchone()
        return EditorialInputRequest.model_validate(value[0]) if value else None

    async def register_upload(
        self,
        *,
        upload_id: UUID,
        owner_id: str,
        object_uri: str,
        file_name: str,
        content_type: str,
        size_bytes: int,
        upload_session_url: str,
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.uploads
                   (upload_id,owner_id,object_uri,file_name,content_type,size_bytes,
                    upload_session_url,status)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,'uploading')""",
                (upload_id, owner_id, object_uri, file_name, content_type,
                 size_bytes, upload_session_url),
            )

    async def get_upload(self, upload_id: UUID, owner_id: str) -> dict[str, Any] | None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                """SELECT object_uri,file_name,content_type,size_bytes,upload_session_url,status
                   FROM hefesto_product.uploads WHERE upload_id=%s AND owner_id=%s""",
                (upload_id, owner_id),
            )
            value = await row.fetchone()
        if not value:
            return None
        return {
            "upload_id": str(upload_id), "object_uri": value[0], "file_name": value[1],
            "content_type": value[2], "size_bytes": value[3],
            "upload_session_url": value[4], "status": value[5],
        }

    async def complete_upload(self, upload_id: UUID, owner_id: str) -> None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                """UPDATE hefesto_product.uploads
                   SET status='stored',upload_session_url=NULL,updated_at=now()
                   WHERE upload_id=%s AND owner_id=%s RETURNING upload_id""",
                (upload_id, owner_id),
            )
            if await row.fetchone() is None:
                raise KeyError("unknown upload")

    async def owns_source(self, owner_id: str, object_uri: str) -> bool:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                """SELECT 1 FROM hefesto_product.uploads
                   WHERE owner_id=%s AND object_uri=%s AND status='stored'""",
                (owner_id, object_uri),
            )
            return await row.fetchone() is not None

    async def record_stages(
        self, execution_run_id: UUID, stages: Sequence[dict[str, object]]
    ) -> None:
        async with self._pool.connection() as connection:
            for stage in stages:
                await connection.execute(
                    """INSERT INTO hefesto_product.stage_receipts
                       (execution_run_id,stage,status,duration_ms,payload)
                       VALUES (%s,%s,%s,%s,%s::jsonb)
                       ON CONFLICT(execution_run_id,stage) DO UPDATE SET
                         status=excluded.status,duration_ms=excluded.duration_ms,
                         payload=excluded.payload,updated_at=now()""",
                    (
                        execution_run_id, str(stage["stage"]),
                        str(stage.get("status", "completed")),
                        int(stage.get("duration_ms", 0)), json.dumps(stage),
                    ),
                )

    async def stage_report(self, execution_run_id: UUID) -> list[dict[str, object]]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT payload FROM hefesto_product.stage_receipts
                   WHERE execution_run_id=%s ORDER BY updated_at,stage""",
                (execution_run_id,),
            )
            return [dict(item[0]) for item in await rows.fetchall()]

    async def save_run(self, run: ProductRun) -> None:
        payload = run.model_dump_json()
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.runs(execution_run_id,project_id,state,payload)
                   VALUES (%s,%s,%s,%s::jsonb)
                   ON CONFLICT(execution_run_id) DO UPDATE SET
                     state=excluded.state,payload=excluded.payload,updated_at=now()""",
                (run.execution_run_id, run.project_id, run.state.value, payload),
            )

    async def get_run(self, execution_run_id: UUID) -> ProductRun | None:
        async with self._pool.connection() as connection:
            row = await connection.execute(
                "SELECT payload FROM hefesto_product.runs WHERE execution_run_id=%s",
                (execution_run_id,),
            )
            value = await row.fetchone()
        if not value:
            return None
        payload = dict(value[0])
        project_record = payload.get("editorial_project")
        if isinstance(project_record, dict):
            proposal = project_record.get("proposal")
            if isinstance(proposal, dict):
                proposal.setdefault("editorial_corpus", None)
        return ProductRun.model_validate(payload)

    async def save_structure(
        self,
        execution_run_id: UUID,
        paragraphs: Sequence[Paragraph],
        assets: Sequence[SourceAsset] = (),
        asset_links: Sequence[ParagraphAssetLink] = (),
    ) -> None:
        async with self._pool.connection() as connection:
            async with connection.cursor() as cursor:
                await cursor.executemany(
                    """INSERT INTO hefesto_product.paragraphs
                       (execution_run_id,paragraph_id,source_id,source_ordinal,paragraph_ordinal,text,payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    [(
                        execution_run_id, paragraph.paragraph_id, paragraph.source_id,
                        paragraph.source_ordinal, paragraph.ordinal, paragraph.text,
                        paragraph.model_dump_json(),
                    ) for paragraph in paragraphs],
                )
                await cursor.executemany(
                        """INSERT INTO hefesto_product.sentences
                           (execution_run_id,paragraph_id,sentence_id,ordinal,text)
                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        [
                            (
                                execution_run_id, paragraph.paragraph_id,
                                sentence.sentence_id, sentence.ordinal, sentence.text,
                            )
                            for paragraph in paragraphs
                            for sentence in paragraph.sentences
                        ],
                    )
                if assets:
                    await cursor.executemany(
                        """INSERT INTO hefesto_product.source_assets
                           (execution_run_id,asset_id,source_id,source_ordinal,
                            asset_ordinal,kind,role,page_no,caption,text_representation,
                            blob_uri,structured_uri,binary_sha256,payload)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)
                           ON CONFLICT DO NOTHING""",
                        [(
                            execution_run_id, asset.asset_id, asset.source_id,
                            asset.source_ordinal, asset.ordinal, asset.kind, asset.role,
                            asset.page_no, asset.caption, asset.text_representation,
                            asset.blob_uri, asset.structured_uri, asset.binary_sha256,
                            asset.model_dump_json(),
                        ) for asset in assets],
                    )
                if asset_links:
                    await cursor.executemany(
                        """INSERT INTO hefesto_product.paragraph_assets
                           (execution_run_id,paragraph_id,asset_id,relation,distance,
                            is_primary,payload)
                           VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)
                           ON CONFLICT DO NOTHING""",
                        [(
                            execution_run_id, link.paragraph_id, link.asset_id,
                            link.relation, link.distance, link.primary,
                            link.model_dump_json(),
                        ) for link in asset_links],
                    )

    async def structure_counts(self, execution_run_id: UUID) -> dict[UUID, tuple[int, int]]:
        """Count rows actually committed for each source, including its sentences."""
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT p.source_id, count(DISTINCT p.paragraph_id), count(s.sentence_id)
                   FROM hefesto_product.paragraphs p
                   LEFT JOIN hefesto_product.sentences s
                     ON s.execution_run_id=p.execution_run_id
                    AND s.paragraph_id=p.paragraph_id
                   WHERE p.execution_run_id=%s
                   GROUP BY p.source_id""",
                (execution_run_id,),
            )
            return {source_id: (paragraphs, sentences)
                    for source_id, paragraphs, sentences in await rows.fetchall()}

    async def save_selection_batch(
        self, execution_run_id: UUID, batch: SelectionBatch
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.selection_batches
                   (execution_run_id,batch_id,source_id,source_ordinal,
                    first_passage_ordinal,last_passage_ordinal,input_tokens,payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                (
                    execution_run_id, batch.batch_id, batch.source_id,
                    batch.source_ordinal, batch.first_passage_ordinal,
                    batch.last_passage_ordinal, batch.input_tokens,
                    batch.model_dump_json(),
                ),
            )

    async def structure(self, execution_run_id: UUID) -> list[Paragraph]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT payload FROM hefesto_product.paragraphs
                   WHERE execution_run_id=%s
                   ORDER BY source_ordinal,paragraph_ordinal""",
                (execution_run_id,),
            )
            values = await rows.fetchall()
        return [Paragraph.model_validate(item[0]) for item in values]

    async def selection_batches(
        self, execution_run_id: UUID
    ) -> list[SelectionBatch]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT payload FROM hefesto_product.selection_batches
                   WHERE execution_run_id=%s
                   ORDER BY source_ordinal,first_passage_ordinal""",
                (execution_run_id,),
            )
            values = await rows.fetchall()
        return [SelectionBatch.model_validate(item[0]) for item in values]

    async def completed_matrix_batches(self, execution_run_id: UUID) -> list[str]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT batch_id FROM hefesto_product.editorial_matrix_batches
                   WHERE execution_run_id=%s AND status='completed'""",
                (execution_run_id,),
            )
            return [str(row[0]).strip() for row in await cursor.fetchall()]

    async def matrix_split_batches(self, execution_run_id: UUID) -> list[str]:
        async with self._pool.connection() as connection:
            cursor = await connection.execute(
                """SELECT batch_id FROM hefesto_product.editorial_matrix_batches
                   WHERE execution_run_id=%s AND status='failed'
                     AND error_message LIKE '%%MAX_TOKENS%%'""",
                (execution_run_id,),
            )
            return [str(row[0]).strip() for row in await cursor.fetchall()]

    async def save_matrix_batch(
        self,
        execution_run_id: UUID,
        batch_id: str,
        version: str,
        input_tokens: int,
        coverage: Sequence[dict[str, Any]],
        claims: Sequence[dict[str, Any]],
    ) -> None:
        """Commit claims, references and examined passages in one transaction."""
        async with self._pool.connection() as connection:
            async with connection.transaction():
                row = await connection.execute(
                    """SELECT status FROM hefesto_product.editorial_matrix_batches
                       WHERE execution_run_id=%s AND batch_id=%s FOR UPDATE""",
                    (execution_run_id, batch_id),
                )
                previous = await row.fetchone()
                if previous and previous[0] == "completed":
                    return
                await connection.execute(
                    """INSERT INTO hefesto_product.editorial_matrix_batches
                       (execution_run_id,batch_id,extraction_version,status,input_tokens,error_message)
                       VALUES (%s,%s,%s,'completed',%s,NULL)
                       ON CONFLICT (execution_run_id,batch_id) DO UPDATE SET
                         status='completed',input_tokens=excluded.input_tokens,
                         error_message=NULL,updated_at=now()""",
                    (execution_run_id, batch_id, version, input_tokens),
                )
                for item in coverage:
                    await connection.execute(
                        """INSERT INTO hefesto_product.editorial_matrix_coverage
                           (execution_run_id,batch_id,passage_id,paragraph_id,
                            source_id,source_ordinal)
                           VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (
                            execution_run_id, batch_id, item["passage_id"],
                            item["paragraph_id"], item["source_id"],
                            item["source_ordinal"],
                        ),
                    )
                for claim in claims:
                    await connection.execute(
                        """INSERT INTO hefesto_product.editorial_claims
                           (execution_run_id,claim_id,batch_id,origin,kind,statement)
                           VALUES (%s,%s,%s,'source',%s,%s)
                           ON CONFLICT DO NOTHING""",
                        (
                            execution_run_id, claim["claim_id"], batch_id,
                            claim["kind"], claim["statement"],
                        ),
                    )
                    for evidence in claim["evidence"]:
                        await connection.execute(
                            """INSERT INTO hefesto_product.editorial_claim_evidence
                               (execution_run_id,claim_id,passage_id,paragraph_id,
                                source_id,source_ordinal,role)
                               VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (
                                execution_run_id, claim["claim_id"],
                                evidence["passage_id"], evidence["paragraph_id"],
                                evidence["source_id"], evidence["source_ordinal"],
                                evidence["role"],
                            ),
                        )

    async def fail_matrix_batch(
        self,
        execution_run_id: UUID,
        batch_id: str,
        version: str,
        input_tokens: int,
        error_message: str,
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.editorial_matrix_batches
                   (execution_run_id,batch_id,extraction_version,status,
                    input_tokens,error_message)
                   VALUES (%s,%s,%s,'failed',%s,%s)
                   ON CONFLICT (execution_run_id,batch_id) DO UPDATE SET
                     status='failed',error_message=excluded.error_message,
                     updated_at=now()
                   WHERE hefesto_product.editorial_matrix_batches.status <> 'completed'""",
                (execution_run_id, batch_id, version, input_tokens, error_message),
            )

    async def matrix_report(self, execution_run_id: UUID) -> dict[str, object]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT status, count(*) FROM hefesto_product.editorial_matrix_batches
                   WHERE execution_run_id=%s GROUP BY status""",
                (execution_run_id,),
            )
            statuses = {status: count for status, count in await rows.fetchall()}
            row = await connection.execute(
                """SELECT count(*) FROM hefesto_product.editorial_matrix_batches
                   WHERE execution_run_id=%s AND status='failed'
                     AND error_message LIKE '%%MAX_TOKENS%%'""",
                (execution_run_id,),
            )
            split_batches = (await row.fetchone())[0]
            row = await connection.execute(
                """SELECT count(DISTINCT passage_id),
                          count(DISTINCT source_id)
                   FROM hefesto_product.editorial_matrix_coverage
                   WHERE execution_run_id=%s""",
                (execution_run_id,),
            )
            passages, sources = await row.fetchone()
            row = await connection.execute(
                """SELECT count(*) FROM hefesto_product.editorial_claims
                   WHERE execution_run_id=%s AND status='active'""",
                (execution_run_id,),
            )
            claims = (await row.fetchone())[0]
        return {
            "execution_run_id": str(execution_run_id),
            "completed_batches": statuses.get("completed", 0),
            "failed_batches": statuses.get("failed", 0) - split_batches,
            "split_batches": split_batches,
            "examined_passages": passages,
            "represented_sources": sources,
            "source_claims": claims,
        }

    async def matrix_claims(self, execution_run_id: UUID) -> list[dict[str, Any]]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT c.claim_id, c.kind, c.statement, c.origin,
                          e.source_ordinal, e.passage_id, e.role
                   FROM hefesto_product.editorial_claims c
                   LEFT JOIN hefesto_product.editorial_claim_evidence e
                     ON e.execution_run_id=c.execution_run_id
                    AND e.claim_id=c.claim_id
                   WHERE c.execution_run_id=%s AND c.status='active'
                   ORDER BY c.created_at,c.claim_id,e.source_ordinal,e.passage_id""",
                (execution_run_id,),
            )
            values = await rows.fetchall()
        claims: dict[str, dict[str, Any]] = {}
        for claim_id, kind, statement, origin, ordinal, passage_id, role in values:
            claim = claims.setdefault(str(claim_id).strip(), {
                "claim_id": str(claim_id).strip(), "kind": kind,
                "statement": statement, "origin": origin, "evidence": [],
            })
            if passage_id is not None:
                claim["evidence"].append({
                    "source_ordinal": ordinal, "passage_id": str(passage_id).strip(),
                    "role": role,
                })
        return list(claims.values())

    async def remap_matrix_subject_links(
        self, execution_run_id: UUID, round_number: int,
        retained_indexes: Sequence[int],
    ) -> None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                rows = await connection.execute(
                    """SELECT subject_index,claim_id,role
                       FROM hefesto_product.editorial_subject_claims
                       WHERE execution_run_id=%s AND round_number=%s""",
                    (execution_run_id, round_number),
                )
                assignments = await rows.fetchall()
                await connection.execute(
                    """DELETE FROM hefesto_product.editorial_subject_claims
                       WHERE execution_run_id=%s AND round_number=%s""",
                    (execution_run_id, round_number),
                )
                mapping = {old: new for new, old in enumerate(retained_indexes)}
                for old, claim_id, role in assignments:
                    if old in mapping:
                        await connection.execute(
                            """INSERT INTO hefesto_product.editorial_subject_claims
                               (execution_run_id,round_number,subject_index,claim_id,role)
                               VALUES (%s,%s,%s,%s,%s)""",
                            (execution_run_id, round_number, mapping[old], claim_id, role),
                        )

    async def matrix_subject_claim_roles(
        self, execution_run_id: UUID, round_number: int,
    ) -> dict[int, list[tuple[str, str]]]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT subject_index,claim_id,role
                   FROM hefesto_product.editorial_subject_claims
                   WHERE execution_run_id=%s AND round_number=%s
                   ORDER BY subject_index,claim_id""",
                (execution_run_id, round_number),
            )
            result: dict[int, list[tuple[str, str]]] = {}
            for index, claim_id, role in await rows.fetchall():
                result.setdefault(index, []).append((str(claim_id).strip(), role))
            return result

    async def matrix_work(
        self, execution_run_id: UUID, round_number: int, task: str,
        unit_id: str, input_digest: str,
    ) -> dict[str, Any] | None:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT payload FROM hefesto_product.editorial_matrix_work
                   WHERE execution_run_id=%s AND round_number=%s
                     AND task=%s AND unit_id=%s AND input_digest=%s""",
                (execution_run_id, round_number, task, unit_id, input_digest),
            )
            row = await rows.fetchone()
            return dict(row[0]) if row else None

    async def save_matrix_work(
        self, execution_run_id: UUID, round_number: int, task: str,
        unit_id: str, input_digest: str, payload: dict[str, Any],
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.editorial_matrix_work
                   (execution_run_id,round_number,task,unit_id,input_digest,payload)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb)
                   ON CONFLICT (execution_run_id,round_number,task,unit_id)
                   DO UPDATE SET input_digest=excluded.input_digest,
                                 payload=excluded.payload,
                                 created_at=now()
                   WHERE hefesto_product.editorial_matrix_work.input_digest
                         <> excluded.input_digest""",
                (execution_run_id, round_number, task, unit_id, input_digest,
                 json.dumps(payload, ensure_ascii=False)),
            )

    async def replace_matrix_subject_links(
        self, execution_run_id: UUID, round_number: int, subject_index: int,
        assignments: Sequence[tuple[str, str]],
    ) -> None:
        async with self._pool.connection() as connection:
            async with connection.transaction():
                await connection.execute(
                    """DELETE FROM hefesto_product.editorial_subject_claims
                       WHERE execution_run_id=%s AND round_number=%s
                         AND subject_index=%s""",
                    (execution_run_id, round_number, subject_index),
                )
                for claim_id, role in assignments:
                    await connection.execute(
                        """INSERT INTO hefesto_product.editorial_subject_claims
                           (execution_run_id,round_number,subject_index,claim_id,role)
                           VALUES (%s,%s,%s,%s,%s)""",
                        (execution_run_id, round_number, subject_index, claim_id, role),
                    )

    async def save_condensation(
        self, execution_run_id: UUID, unit: EditorialCondensation
    ) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.editorial_condensations
                   (execution_run_id,unit_id,level,ordinal,input_tokens,payload)
                   VALUES (%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                (
                    execution_run_id, unit.unit_id, unit.level, unit.ordinal,
                    unit.input_tokens, unit.model_dump_json(),
                ),
            )

    async def condensations(
        self, execution_run_id: UUID
    ) -> list[EditorialCondensation]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT payload FROM hefesto_product.editorial_condensations
                   WHERE execution_run_id=%s ORDER BY level,ordinal""",
                (execution_run_id,),
            )
            values = await rows.fetchall()
        return [EditorialCondensation.model_validate(item[0]) for item in values]

    async def work_report(self, execution_run_id: UUID) -> dict[str, object]:
        batches = await self.selection_batches(execution_run_id)
        condensations = await self.condensations(execution_run_id)
        selected: set[tuple[int, int]] = set()
        for batch in batches:
            flags = batch.flags
            ordinals = {
                *flags.relevant, *flags.central, *flags.uncertain,
                *flags.include_previous, *flags.include_next,
                *(ordinal - 1 for ordinal in flags.include_previous),
                *(ordinal + 1 for ordinal in flags.include_next),
            }
            selected.update(
                (batch.source_ordinal, ordinal)
                for ordinal in ordinals if ordinal > 0
            )
        return {
            "selection_batches": len(batches),
            "selected_passages": len(selected),
            "condensation_units": len(condensations),
        }

    async def record_usage(self, execution_run_id: UUID, project_id: UUID, usage: CallUsage) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.call_usage
                   (execution_run_id,project_id,task_type,status,prompt_tokens,cached_tokens,
                    output_tokens,reasoning_tokens,total_tokens,latency_ms,rated_cost_usd,payload)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                (
                    execution_run_id, project_id, usage.task_type, usage.status,
                    usage.prompt_tokens, usage.cached_tokens, usage.output_tokens,
                    usage.reasoning_tokens, usage.total_tokens, usage.latency_ms,
                    usage.rated_cost_usd, usage.model_dump_json(),
                ),
            )

    async def usage_report(self, execution_run_id: UUID) -> dict[str, object]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                "SELECT payload FROM hefesto_product.call_usage WHERE execution_run_id=%s ORDER BY id",
                (execution_run_id,),
            )
            calls = [CallUsage.model_validate(item[0]) for item in await rows.fetchall()]
        return {
            "call_count": len(calls),
            "prompt_tokens": sum(item.prompt_tokens for item in calls),
            "cached_tokens": sum(item.cached_tokens for item in calls),
            "output_tokens": sum(item.output_tokens for item in calls),
            "reasoning_tokens": sum(item.reasoning_tokens for item in calls),
            "total_tokens": sum(item.total_tokens for item in calls),
            "provider_latency_ms": sum(item.latency_ms for item in calls),
            "rated_cost_usd": str(sum((Decimal(item.rated_cost_usd or "0") for item in calls), start=Decimal("0"))),
            "calls": [item.model_dump(mode="json") for item in calls],
        }

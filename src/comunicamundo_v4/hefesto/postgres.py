from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool

from .contracts import CallUsage, Paragraph, ParagraphNote, ProductRun


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
        return ProductRun.model_validate(value[0]) if value else None

    async def save_structure(self, execution_run_id: UUID, paragraphs: Sequence[Paragraph]) -> None:
        async with self._pool.connection() as connection:
            for paragraph in paragraphs:
                await connection.execute(
                    """INSERT INTO hefesto_product.paragraphs
                       (execution_run_id,paragraph_id,source_id,source_ordinal,paragraph_ordinal,text,payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                    (
                        execution_run_id, paragraph.paragraph_id, paragraph.source_id,
                        paragraph.source_ordinal, paragraph.ordinal, paragraph.text,
                        paragraph.model_dump_json(),
                    ),
                )
                for sentence in paragraph.sentences:
                    await connection.execute(
                        """INSERT INTO hefesto_product.sentences
                           (execution_run_id,paragraph_id,sentence_id,ordinal,text)
                           VALUES (%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (execution_run_id, paragraph.paragraph_id, sentence.sentence_id, sentence.ordinal, sentence.text),
                    )

    async def save_note(self, execution_run_id: UUID, note: ParagraphNote) -> None:
        async with self._pool.connection() as connection:
            await connection.execute(
                """INSERT INTO hefesto_product.paragraph_notes
                   (execution_run_id,paragraph_id,source_id,payload)
                   VALUES (%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING""",
                (execution_run_id, note.paragraph_id, note.source_id, note.model_dump_json()),
            )

    async def notes(self, execution_run_id: UUID) -> list[ParagraphNote]:
        async with self._pool.connection() as connection:
            rows = await connection.execute(
                """SELECT n.payload FROM hefesto_product.paragraph_notes n
                   JOIN hefesto_product.paragraphs p USING(execution_run_id,paragraph_id)
                   WHERE n.execution_run_id=%s ORDER BY p.source_ordinal,p.paragraph_ordinal""",
                (execution_run_id,),
            )
            values = await rows.fetchall()
        return [ParagraphNote.model_validate(item[0]) for item in values]

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

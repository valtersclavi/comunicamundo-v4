from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Sequence
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

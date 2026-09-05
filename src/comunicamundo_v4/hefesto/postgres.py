"""PostgreSQL foundation for durable Hefesto execution and source knowledge."""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import asynccontextmanager
from hashlib import sha256
import json
import os
from pathlib import Path
from datetime import datetime
from typing import Any, Generic, TypeVar
from uuid import NAMESPACE_URL, UUID, uuid5

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import AsyncConnectionPool
from pydantic import BaseModel

from .contracts import NormalizedSource
from .digestion_contracts import DigestionExecutionRun, DigestionPlan, SectionDigest, SourceDossier, canonical_sha256
from .editorial_v2_contracts import EditorialV2ExecutionRun
from .execution_contracts import LLMAttemptRecord, WorkUnit
from .production_v2_contracts import ProductionExecutionRunV2


ModelT = TypeVar("ModelT", bound=BaseModel)
MIGRATIONS_DIR = Path(
    os.environ.get("HEFESTO_MIGRATIONS_DIR", str(Path.cwd() / "migrations"))
).resolve()


def _json(model: BaseModel | dict[str, Any] | list[Any]) -> Jsonb:
    value = model.model_dump(mode="json") if isinstance(model, BaseModel) else model
    return Jsonb(value)


class PostgresDatabase:
    """Small lazy async pool; one instance is shared by a Cloud Run execution."""

    def __init__(self, conninfo: str, *, min_size: int = 0, max_size: int = 10) -> None:
        if not conninfo.strip():
            raise ValueError("PostgreSQL connection URL cannot be empty")
        self._pool = AsyncConnectionPool(
            conninfo=conninfo,
            min_size=min_size,
            max_size=max_size,
            open=False,
            kwargs={"row_factory": dict_row},
        )
        self._open_lock = asyncio.Lock()

    @classmethod
    def from_runtime_environment(cls, *, max_size: int = 10) -> "PostgresDatabase":
        value = os.environ.get("HEFESTO_POSTGRES_URL", "").strip()
        if not value:
            raise ValueError("missing required environment variable: HEFESTO_POSTGRES_URL")
        return cls(value, max_size=max_size)

    async def open(self) -> None:
        if not self._pool.closed:
            return
        async with self._open_lock:
            if self._pool.closed:
                await self._pool.open()

    @asynccontextmanager
    async def connection(self) -> AsyncIterator[psycopg.AsyncConnection]:
        await self.open()
        async with self._pool.connection() as connection:
            yield connection

    async def close(self) -> None:
        if not self._pool.closed:
            await self._pool.close()

    async def check(self) -> dict[str, Any]:
        async with self.connection() as connection:
            row = await (
                await connection.execute(
                    """SELECT current_database() AS database,
                              current_user AS role,
                              current_setting('server_version') AS postgres_version,
                              EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector') AS pgvector"""
                )
            ).fetchone()
        assert row is not None
        return dict(row)


async def apply_migrations(conninfo: str, *, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply immutable SQL files in lexical order using the direct Neon URL."""

    files = sorted(migrations_dir.glob("*.sql"))
    if not files:
        raise ValueError(f"no PostgreSQL migrations found in {migrations_dir}")
    applied: list[str] = []
    connection = await psycopg.AsyncConnection.connect(conninfo, row_factory=dict_row)
    try:
        await connection.execute("CREATE SCHEMA IF NOT EXISTS hefesto")
        await connection.execute(
            """CREATE TABLE IF NOT EXISTS hefesto.schema_migrations (
                   version text PRIMARY KEY,
                   checksum char(64) NOT NULL,
                   applied_at timestamptz NOT NULL DEFAULT now()
               )"""
        )
        await connection.commit()
        for path in files:
            data = path.read_bytes()
            checksum = sha256(data).hexdigest()
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        "SELECT checksum FROM hefesto.schema_migrations WHERE version = %s FOR UPDATE",
                        (path.name,),
                    )
                ).fetchone()
                if row is not None:
                    if row["checksum"] != checksum:
                        raise ValueError(f"migration checksum changed after application: {path.name}")
                    continue
                await connection.execute(data.decode("utf-8"))
                await connection.execute(
                    "INSERT INTO hefesto.schema_migrations(version, checksum) VALUES (%s, %s)",
                    (path.name, checksum),
                )
                applied.append(path.name)
    finally:
        await connection.close()
    return applied


class _JsonRunRepository(Generic[ModelT]):
    def __init__(self, database: PostgresDatabase, *, table: str, schema: type[ModelT]) -> None:
        self._database = database
        self._table = table
        self._schema = schema
        if table not in {"digestion_runs", "editorial_runs", "production_runs"}:
            raise ValueError("unsupported run table")

    async def create(self, run: ModelT) -> ModelT:
        values: list[Any] = [run.execution_run_id, run.project_id]
        columns = ["execution_run_id", "project_id"]
        if isinstance(run, ProductionExecutionRunV2):
            columns.append("editorial_project_id")
            values.append(run.editorial_project_id)
        columns.extend(["state", "payload", "created_at", "updated_at"])
        values.extend([run.state.value, _json(run), run.created_at, run.updated_at])
        placeholders = ", ".join(["%s"] * len(values))
        async with self._database.connection() as connection:
            async with connection.transaction():
                result = await connection.execute(
                    f"INSERT INTO hefesto.{self._table} ({', '.join(columns)}) VALUES ({placeholders}) ON CONFLICT DO NOTHING",
                    values,
                )
                if result.rowcount == 0:
                    existing = await self._get(connection, run.execution_run_id)
                    if existing != run:
                        raise ValueError(f"conflicting {self._table} identity {run.execution_run_id}")
        return run

    async def get(self, execution_run_id: UUID) -> ModelT | None:
        async with self._database.connection() as connection:
            return await self._get(connection, execution_run_id)

    async def transition(self, execution_run_id: UUID, change: Callable[[ModelT], ModelT]) -> ModelT:
        async with self._database.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        f"SELECT payload, revision FROM hefesto.{self._table} WHERE execution_run_id = %s FOR UPDATE",
                        (execution_run_id,),
                    )
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown {self._table} identity {execution_run_id}")
                current = self._schema.model_validate(row["payload"])
                updated = change(current)
                if updated.execution_run_id != current.execution_run_id or updated.project_id != current.project_id:
                    raise ValueError("run identity is immutable")
                if isinstance(current, ProductionExecutionRunV2) and updated.editorial_project_id != current.editorial_project_id:
                    raise ValueError("production editorial project identity is immutable")
                result = await connection.execute(
                    f"""UPDATE hefesto.{self._table}
                        SET state = %s, payload = %s, updated_at = %s, revision = revision + 1
                        WHERE execution_run_id = %s AND revision = %s""",
                    (updated.state.value, _json(updated), updated.updated_at, execution_run_id, row["revision"]),
                )
                if result.rowcount != 1:
                    raise RuntimeError(f"concurrent update for {self._table} {execution_run_id}")
                return updated

    async def _get(self, connection: psycopg.AsyncConnection, execution_run_id: UUID) -> ModelT | None:
        row = await (
            await connection.execute(
                f"SELECT payload FROM hefesto.{self._table} WHERE execution_run_id = %s",
                (execution_run_id,),
            )
        ).fetchone()
        return None if row is None else self._schema.model_validate(row["payload"])


class PostgresExecutionRunRepository(_JsonRunRepository[DigestionExecutionRun]):
    def __init__(self, database: PostgresDatabase) -> None:
        super().__init__(database, table="digestion_runs", schema=DigestionExecutionRun)


class PostgresEditorialV2RunRepository(_JsonRunRepository[EditorialV2ExecutionRun]):
    def __init__(self, database: PostgresDatabase) -> None:
        super().__init__(database, table="editorial_runs", schema=EditorialV2ExecutionRun)


class PostgresProductionV2RunRepository(_JsonRunRepository[ProductionExecutionRunV2]):
    def __init__(self, database: PostgresDatabase) -> None:
        super().__init__(database, table="production_runs", schema=ProductionExecutionRunV2)


class PostgresWorkRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def create(self, unit: WorkUnit) -> WorkUnit:
        async with self._database.connection() as connection:
            async with connection.transaction():
                result = await connection.execute(
                    """INSERT INTO hefesto.work_units
                       (execution_run_id, work_unit_id, project_id, unit_type, status,
                        attempt_count, payload, created_at, updated_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (unit.execution_run_id, unit.work_unit_id, unit.project_id, unit.unit_type,
                     unit.status.value, unit.attempt_count, _json(unit), unit.created_at, unit.updated_at),
                )
                if result.rowcount == 0:
                    existing = await self._get(connection, unit.execution_run_id, unit.work_unit_id)
                    if existing != unit:
                        raise ValueError(f"conflicting immutable work unit {unit.work_unit_id}")
        return unit

    async def get(self, execution_run_id: UUID, work_unit_id: str) -> WorkUnit | None:
        async with self._database.connection() as connection:
            return await self._get(connection, execution_run_id, work_unit_id)

    async def transition(self, execution_run_id: UUID, work_unit_id: str, change: Callable[[WorkUnit], WorkUnit]) -> WorkUnit:
        async with self._database.connection() as connection:
            async with connection.transaction():
                row = await (
                    await connection.execute(
                        """SELECT payload, revision FROM hefesto.work_units
                           WHERE execution_run_id=%s AND work_unit_id=%s FOR UPDATE""",
                        (execution_run_id, work_unit_id),
                    )
                ).fetchone()
                if row is None:
                    raise KeyError(f"unknown work unit {work_unit_id}")
                current = WorkUnit.model_validate(row["payload"])
                updated = change(current)
                if (updated.execution_run_id, updated.work_unit_id, updated.project_id) != (
                    current.execution_run_id, current.work_unit_id, current.project_id
                ):
                    raise ValueError("work-unit identity is immutable")
                result = await connection.execute(
                    """UPDATE hefesto.work_units SET status=%s, attempt_count=%s, payload=%s,
                       updated_at=%s, revision=revision+1
                       WHERE execution_run_id=%s AND work_unit_id=%s AND revision=%s""",
                    (updated.status.value, updated.attempt_count, _json(updated), updated.updated_at,
                     execution_run_id, work_unit_id, row["revision"]),
                )
                if result.rowcount != 1:
                    raise RuntimeError(f"concurrent update for work unit {work_unit_id}")
                return updated

    @staticmethod
    async def _get(connection: psycopg.AsyncConnection, execution_run_id: UUID, work_unit_id: str) -> WorkUnit | None:
        row = await (
            await connection.execute(
                "SELECT payload FROM hefesto.work_units WHERE execution_run_id=%s AND work_unit_id=%s",
                (execution_run_id, work_unit_id),
            )
        ).fetchone()
        return None if row is None else WorkUnit.model_validate(row["payload"])


class PostgresUsageLedger:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def append(self, record: LLMAttemptRecord) -> None:
        usage = record.usage
        values = (
            record.attempt_id, record.call_id, record.execution_run_id, record.project_id,
            record.work_unit_id, record.task_type, record.model, record.status.value, record.attempt,
            record.queued_at, record.started_at, record.finished_at, record.queue_wait_ms,
            record.latency_ms, usage.prompt_tokens if usage else None,
            usage.cached_tokens if usage else None, usage.output_tokens if usage else None,
            usage.reasoning_tokens if usage else None, usage.total_tokens if usage else None,
            usage.provider_reported if usage else None, record.rated_cost_usd, _json(record),
        )
        async with self._database.connection() as connection:
            async with connection.transaction():
                result = await connection.execute(
                    """INSERT INTO hefesto.llm_attempts
                       (attempt_id,call_id,execution_run_id,project_id,work_unit_id,task_type,model,status,attempt,
                        queued_at,started_at,finished_at,queue_wait_ms,latency_ms,prompt_tokens,cached_tokens,
                        output_tokens,reasoning_tokens,total_tokens,provider_reported,rated_cost_usd,payload)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT DO NOTHING""",
                    values,
                )
                if result.rowcount == 0:
                    row = await (await connection.execute(
                        "SELECT payload FROM hefesto.llm_attempts WHERE attempt_id=%s", (record.attempt_id,)
                    )).fetchone()
                    if row is None or LLMAttemptRecord.model_validate(row["payload"]) != record:
                        raise ValueError(f"conflicting immutable LLM attempt {record.attempt_id}")


class PostgresUsageReader:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def list_for_project(self, project_id: UUID) -> list[LLMAttemptRecord]:
        async with self._database.connection() as connection:
            rows = await (await connection.execute(
                "SELECT payload FROM hefesto.llm_attempts WHERE project_id=%s ORDER BY started_at, attempt_id",
                (project_id,),
            )).fetchall()
        return [LLMAttemptRecord.model_validate(row["payload"]) for row in rows]


class PostgresStageRecorder:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def record(
        self,
        *,
        execution_run_id: UUID,
        project_id: UUID,
        stage: str,
        status: str,
        started_at: datetime,
        finished_at: datetime,
        details: dict[str, Any] | None = None,
    ) -> None:
        if status not in {"succeeded", "failed"}:
            raise ValueError("stage status must be succeeded or failed")
        duration_ms = max(0, int((finished_at - started_at).total_seconds() * 1000))
        identity = uuid5(
            NAMESPACE_URL,
            f"hefesto:stage:{execution_run_id}:{stage}:{started_at.isoformat()}",
        )
        payload = details or {}
        async with self._database.connection() as connection:
            async with connection.transaction():
                result = await connection.execute(
                    """INSERT INTO hefesto.stage_events
                       (stage_event_id,execution_run_id,project_id,stage,status,started_at,finished_at,duration_ms,details)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (identity, execution_run_id, project_id, stage, status, started_at,
                     finished_at, duration_ms, _json(payload)),
                )
                if result.rowcount == 0:
                    row = await (await connection.execute(
                        "SELECT status,duration_ms,details FROM hefesto.stage_events WHERE stage_event_id=%s",
                        (identity,),
                    )).fetchone()
                    if row is None or (row["status"], row["duration_ms"], row["details"]) != (
                        status, duration_ms, payload
                    ):
                        raise ValueError(f"conflicting immutable stage event {identity}")


class PostgresKnowledgeRepository:
    """Normalizes immutable source coordinates and direct sentence evidence anchors."""

    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def persist_plan(self, plan: DigestionPlan, sources: Sequence[NormalizedSource]) -> None:
        by_id = {source.source_id: source for source in sources}
        expected_ids = {item.source_id for item in plan.sources}
        if set(by_id) != expected_ids or len(by_id) != len(sources):
            raise ValueError("source collection disagrees with digestion plan")
        source_rows: list[tuple[Any, ...]] = []
        section_rows: list[tuple[Any, ...]] = []
        paragraph_rows: list[tuple[Any, ...]] = []
        sentence_rows: list[tuple[Any, ...]] = []
        source_hashes: dict[str, str] = {}
        section_hashes: dict[str, str] = {}
        paragraph_hashes: dict[str, str] = {}
        sentence_hashes: dict[str, str] = {}
        for source_plan in plan.sources:
            source = by_id[source_plan.source_id]
            source_rows.append((
                plan.execution_run_id, source.source_id, plan.project_id, source_plan.source_ordinal,
                source.title, source.language, source_plan.source_sha256, source_plan.source_char_count,
                source_plan.estimated_source_tokens, source.text, _json(source.metadata),
            ))
            source_hashes[str(source.source_id)] = source_plan.source_sha256
            for section in source_plan.sections:
                section_rows.append((
                    plan.execution_run_id, source.source_id, section.section_id, section.ordinal,
                    section.char_start, section.char_end, section.content_sha256,
                    section.estimated_tokens, section.heading_hint,
                ))
                section_hashes[section.section_id] = section.content_sha256
                for paragraph in section.paragraphs:
                    paragraph_rows.append((
                        plan.execution_run_id, source.source_id, section.section_id, paragraph.paragraph_id,
                        paragraph.ordinal, paragraph.char_start, paragraph.char_end,
                        paragraph.content_sha256, paragraph.text,
                    ))
                    paragraph_hashes[paragraph.paragraph_id] = paragraph.content_sha256
                    for sentence in paragraph.sentences:
                        sentence_rows.append((
                            plan.execution_run_id, source.source_id, paragraph.paragraph_id,
                            sentence.sentence_id, sentence.ordinal, sentence.char_start,
                            sentence.char_end, sentence.content_sha256, sentence.text,
                        ))
                        sentence_hashes[sentence.sentence_id] = sentence.content_sha256
        async with self._database.connection() as connection:
            async with connection.transaction():
                async with connection.cursor() as cursor:
                    await cursor.executemany(
                        """INSERT INTO hefesto.sources
                           (execution_run_id,source_id,project_id,source_ordinal,title,language,content_sha256,
                            char_count,estimated_tokens,normalized_text,metadata)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        source_rows,
                    )
                    await cursor.executemany(
                        """INSERT INTO hefesto.source_sections
                           (execution_run_id,source_id,section_id,ordinal,char_start,char_end,content_sha256,
                            estimated_tokens,heading_hint) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        section_rows,
                    )
                    await cursor.executemany(
                        """INSERT INTO hefesto.source_paragraphs
                           (execution_run_id,source_id,section_id,paragraph_id,ordinal,char_start,char_end,
                            content_sha256,text) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        paragraph_rows,
                    )
                    await cursor.executemany(
                        """INSERT INTO hefesto.source_sentences
                           (execution_run_id,source_id,paragraph_id,sentence_id,ordinal,char_start,
                            char_end,content_sha256,text) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT DO NOTHING""",
                        sentence_rows,
                    )
                checks = (
                    ("sources", "source_id", source_hashes),
                    ("source_sections", "section_id", section_hashes),
                    ("source_paragraphs", "paragraph_id", paragraph_hashes),
                    ("source_sentences", "sentence_id", sentence_hashes),
                )
                for table, identity_column, expected in checks:
                    rows = await (await connection.execute(
                        f"SELECT {identity_column}, content_sha256 FROM hefesto.{table} WHERE execution_run_id=%s",
                        (plan.execution_run_id,),
                    )).fetchall()
                    actual = {str(row[identity_column]): row["content_sha256"] for row in rows}
                    if actual != expected:
                        raise ValueError(f"conflicting immutable relational source data in {table}")

    async def persist_findings(self, plan: DigestionPlan, output: SectionDigest | SourceDossier) -> None:
        source_id = output.source_id
        dossier_id = output.dossier_id if isinstance(output, SourceDossier) else None
        section_id = output.section_id if isinstance(output, SectionDigest) else None
        async with self._database.connection() as connection:
            async with connection.transaction():
                for finding in output.findings:
                    identity = canonical_sha256({
                        "run": str(plan.execution_run_id), "source": str(source_id),
                        "dossier": str(dossier_id) if dossier_id else None, "section": section_id,
                        "finding": finding.model_dump(mode="json"),
                    })
                    await connection.execute(
                        """INSERT INTO hefesto.source_findings
                           (finding_id,execution_run_id,project_id,source_id,dossier_id,section_id,finding,
                            causal_status,evidence_strength,qualifications,payload)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                        (identity, plan.execution_run_id, plan.project_id, source_id, dossier_id, section_id,
                         finding.finding, finding.causal_status.value, finding.evidence_strength.value,
                         _json(finding.qualifications), _json(finding)),
                    )
                    for ordinal, anchor in enumerate(finding.anchors):
                        await connection.execute(
                            """INSERT INTO hefesto.evidence_anchors
                               (finding_id,anchor_ordinal,execution_run_id,paragraph_id,first_sentence_id,
                                last_sentence_id,locator,excerpt,payload)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                            (identity, ordinal, plan.execution_run_id, anchor.paragraph_id,
                             anchor.first_sentence_id, anchor.last_sentence_id, anchor.locator,
                             anchor.excerpt, _json(anchor)),
                        )


async def check_direct_connection(conninfo: str) -> dict[str, Any]:
    database = PostgresDatabase(conninfo, max_size=1)
    try:
        return await database.check()
    finally:
        await database.close()

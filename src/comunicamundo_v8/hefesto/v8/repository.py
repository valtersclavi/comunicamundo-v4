from contextlib import asynccontextmanager
from datetime import UTC, datetime
import json
from uuid import UUID
from ..postgres import ProductRepository
from .contracts import State


class Busy(RuntimeError):
    pass


class Repository:
    """Reuse the existing Postgres pool; session lock excludes API/worker overlap.

    Reads during generation do not lock, so progress remains visible. A crashed
    process releases the session lock. Payload revisions also detect stale saves.
    """
    def __init__(self, product: ProductRepository):
        self.product = product

    @asynccontextmanager
    async def lock(self, execution_id: UUID):
        async with self.product._pool.connection() as c:
            row = await (await c.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s, 8))",
                (str(execution_id),))).fetchone()
            await c.commit()
            if not row[0]:
                raise Busy("project already has an active operation")
            try:
                yield
            finally:
                await c.execute("SELECT pg_advisory_unlock(hashtextextended(%s, 8))",
                                (str(execution_id),))
                await c.commit()

    async def create(self, state: State):
        async with self.product._pool.connection() as c:
            await c.execute(
                """INSERT INTO hefesto_product.v8_projects
                (execution_id,owner_id,revision,status,payload) VALUES (%s,%s,0,%s,%s::jsonb)
                ON CONFLICT(execution_id) DO NOTHING""",
                (state.execution_id, state.owner_id, state.status, state.model_dump_json()))

    async def get(self, execution_id: UUID) -> State:
        async with self.product._pool.connection() as c:
            row = await (await c.execute(
                "SELECT payload FROM hefesto_product.v8_projects WHERE execution_id=%s",
                (execution_id,))).fetchone()
        if not row:
            raise KeyError("unknown V8 execution")
        return State.model_validate(row[0])

    async def save(self, state: State):
        old = state.revision
        # A failed write must not advance the in-memory revision. Otherwise the
        # next save uses a revision that never existed in PostgreSQL.
        pending = state.model_copy(update=dict(revision=old+1,
                                              updated_at=datetime.now(UTC).isoformat()))
        async with self.product._pool.connection() as c:
            cur = await c.execute(
                """UPDATE hefesto_product.v8_projects SET revision=%s,status=%s,
                payload=%s::jsonb,updated_at=now() WHERE execution_id=%s AND revision=%s""",
                (pending.revision, pending.status, pending.model_dump_json(), state.execution_id, old))
            if cur.rowcount != 1:
                raise Busy("stale V8 state revision")
        state.revision = pending.revision
        state.updated_at = pending.updated_at

    async def list(self, owner_id: str):
        async with self.product._pool.connection() as c:
            rows = await (await c.execute(
                """SELECT execution_id,status,payload->'request'->'brief',updated_at
                FROM hefesto_product.v8_projects WHERE owner_id=%s ORDER BY updated_at DESC""",
                (owner_id,))).fetchall()
        return [dict(execution_id=str(r[0]), status=r[1], brief=r[2], updated_at=str(r[3])) for r in rows]

    async def operation(self, execution_id: UUID, key: str):
        async with self.product._pool.connection() as c:
            row = await (await c.execute(
                "SELECT payload FROM hefesto_product.v8_operations WHERE execution_id=%s AND operation_key=%s",
                (execution_id, key))).fetchone()
        return row[0] if row else None

    async def save_operation(self, execution_id: UUID, key: str, payload: dict):
        async with self.product._pool.connection() as c:
            await c.execute(
                """INSERT INTO hefesto_product.v8_operations(execution_id,operation_key,payload)
                VALUES (%s,%s,%s::jsonb) ON CONFLICT(execution_id,operation_key)
                DO UPDATE SET payload=excluded.payload,updated_at=now()""",
                (execution_id, key, json.dumps(payload, ensure_ascii=False)))

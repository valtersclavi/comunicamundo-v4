from __future__ import annotations

from decimal import Decimal
from hashlib import sha256
import json
from pathlib import Path
from typing import Any, Sequence
from uuid import UUID

import psycopg
from psycopg_pool import AsyncConnectionPool


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



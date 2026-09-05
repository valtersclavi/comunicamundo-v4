"""Deterministic publication renderer for an approved final article."""
from __future__ import annotations

from datetime import UTC, datetime
from hashlib import sha256
from html import escape
import json
import re
from typing import Any
from uuid import UUID, uuid5, NAMESPACE_URL

from google.api_core.exceptions import AlreadyExists, PreconditionFailed
from markdown_it import MarkdownIt
from pydantic import Field

from .contracts import FinalArticle, StrictModel
from .digestion_contracts import ArtifactPointer
from .postgres import PostgresDatabase, _json


class RenderedArticle(StrictModel):
    schema_version: int = 1
    rendered_product_id: UUID
    execution_run_id: UUID
    project_id: UUID
    editorial_project_id: UUID
    product_type: str = "article"
    title: str = Field(min_length=1)
    language: str = Field(min_length=2)
    word_count: int = Field(ge=1)
    quality_score: int = Field(ge=0, le=100)
    markdown: ArtifactPointer
    html: ArtifactPointer
    created_at: datetime


class ArticleRenderer:
    def __init__(self) -> None:
        self._markdown = MarkdownIt(
            "commonmark",
            {"html": False, "linkify": False, "typographer": True},
        )

    def render(self, article: FinalArticle, *, language: str = "pt-BR") -> tuple[str, str]:
        body = article.body_markdown.strip()
        if not _starts_with_same_h1(body, article.title):
            body = f"# {article.title}\n\n{body}"
        rendered_body = self._markdown.render(body)
        html = _html_document(
            title=article.title,
            language=language,
            rendered_body=rendered_body,
        )
        return body + "\n", html


class GcsRenderedArticleStore:
    def __init__(self, storage_client: Any, *, bucket_name: str, root_prefix: str) -> None:
        self._bucket = storage_client.bucket(bucket_name)
        self._bucket_name = bucket_name
        self._root = root_prefix.strip("/")

    def persist_bytes(
        self,
        *,
        execution_run_id: UUID,
        project_id: UUID,
        name: str,
        data: bytes,
        content_type: str,
    ) -> ArtifactPointer:
        object_name = (
            f"{self._root}/projects/{project_id}/executions/"
            f"{execution_run_id}/renderer/{name}"
        )
        blob = self._bucket.blob(object_name)
        try:
            blob.upload_from_string(data, content_type=content_type, if_generation_match=0)
        except (AlreadyExists, PreconditionFailed):
            if blob.download_as_bytes() != data:
                raise ValueError(f"conflicting immutable rendered artifact: {name}")
        return ArtifactPointer(
            uri=f"gs://{self._bucket_name}/{object_name}",
            sha256=sha256(data).hexdigest(),
        )


def persist_complete_run_receipt(
    storage_client: Any,
    *,
    bucket_name: str,
    root_prefix: str,
    project_id: UUID,
    execution_run_id: UUID,
    payload: dict[str, Any],
) -> ArtifactPointer:
    """Persist the immutable machine-readable receipt used by proof workflows."""

    encoded = json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    digest = sha256(encoded).hexdigest()
    object_name = (
        f"{root_prefix.strip('/')}/projects/{project_id}/executions/"
        f"{execution_run_id}/complete-run.json"
    )
    blob = storage_client.bucket(bucket_name).blob(object_name)
    try:
        blob.upload_from_string(encoded, content_type="application/json", if_generation_match=0)
    except (AlreadyExists, PreconditionFailed):
        if blob.download_as_bytes() != encoded:
            raise ValueError("conflicting immutable complete-run receipt")
    return ArtifactPointer(uri=f"gs://{bucket_name}/{object_name}", sha256=digest)


class PostgresRenderedProductRepository:
    def __init__(self, database: PostgresDatabase) -> None:
        self._database = database

    async def persist(self, product: RenderedArticle, manifest: ArtifactPointer) -> None:
        async with self._database.connection() as connection:
            async with connection.transaction():
                result = await connection.execute(
                    """INSERT INTO hefesto.rendered_products
                       (rendered_product_id,execution_run_id,project_id,editorial_project_id,product_type,
                        markdown_uri,markdown_sha256,html_uri,html_sha256,manifest_uri,manifest_sha256,
                        payload,created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (product.rendered_product_id, product.execution_run_id, product.project_id,
                     product.editorial_project_id, product.product_type, product.markdown.uri,
                     product.markdown.sha256, product.html.uri, product.html.sha256, manifest.uri,
                     manifest.sha256, _json(product), product.created_at),
                )
                if result.rowcount == 0:
                    row = await (await connection.execute(
                        """SELECT payload, manifest_sha256 FROM hefesto.rendered_products
                           WHERE execution_run_id=%s AND product_type=%s""",
                        (product.execution_run_id, product.product_type),
                    )).fetchone()
                    if row is None or RenderedArticle.model_validate(row["payload"]) != product or row["manifest_sha256"] != manifest.sha256:
                        raise ValueError("conflicting immutable rendered product")


async def render_and_persist_article(
    *,
    database: PostgresDatabase,
    storage_client: Any,
    bucket_name: str,
    root_prefix: str,
    execution_run_id: UUID,
    article: FinalArticle,
    language: str = "pt-BR",
) -> tuple[RenderedArticle, ArtifactPointer]:
    markdown, html = ArticleRenderer().render(article, language=language)
    store = GcsRenderedArticleStore(
        storage_client,
        bucket_name=bucket_name,
        root_prefix=root_prefix,
    )
    markdown_pointer = store.persist_bytes(
        execution_run_id=execution_run_id,
        project_id=article.project_id,
        name="article.md",
        data=markdown.encode("utf-8"),
        content_type="text/markdown; charset=utf-8",
    )
    html_pointer = store.persist_bytes(
        execution_run_id=execution_run_id,
        project_id=article.project_id,
        name="article.html",
        data=html.encode("utf-8"),
        content_type="text/html; charset=utf-8",
    )
    created_at = datetime.now(UTC)
    product = RenderedArticle(
        rendered_product_id=uuid5(NAMESPACE_URL, f"hefesto:rendered-article:{execution_run_id}"),
        execution_run_id=execution_run_id,
        project_id=article.project_id,
        editorial_project_id=article.editorial_project_id,
        title=article.title,
        language=language,
        word_count=article.word_count,
        quality_score=article.quality_score,
        markdown=markdown_pointer,
        html=html_pointer,
        created_at=created_at,
    )
    manifest_data = product.model_dump_json().encode("utf-8")
    manifest_pointer = store.persist_bytes(
        execution_run_id=execution_run_id,
        project_id=article.project_id,
        name="manifest.json",
        data=manifest_data,
        content_type="application/json",
    )
    await PostgresRenderedProductRepository(database).persist(product, manifest_pointer)
    return product, manifest_pointer


def _starts_with_same_h1(markdown: str, title: str) -> bool:
    first_line = markdown.splitlines()[0].strip() if markdown.splitlines() else ""
    match = re.fullmatch(r"#\s+(.+?)\s*#*", first_line)
    return bool(match and match.group(1).strip().casefold() == title.strip().casefold())


def _html_document(*, title: str, language: str, rendered_body: str) -> str:
    safe_title = escape(title)
    safe_language = escape(language, quote=True)
    return f"""<!doctype html>
<html lang="{safe_language}">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{safe_title}</title>
  <style>
    :root {{ color-scheme: light; --ink: #172033; --muted: #59657a; --paper: #fff; --accent: #3157a4; }}
    * {{ box-sizing: border-box; }}
    body {{ margin: 0; background: #f4f6fa; color: var(--ink); font-family: Georgia, 'Times New Roman', serif; line-height: 1.72; }}
    article {{ width: min(100% - 2rem, 760px); margin: 3rem auto; padding: clamp(1.5rem, 5vw, 4rem); background: var(--paper); box-shadow: 0 12px 40px rgba(25,35,55,.09); }}
    h1, h2, h3 {{ font-family: Inter, ui-sans-serif, system-ui, sans-serif; line-height: 1.18; letter-spacing: -.02em; }}
    h1 {{ font-size: clamp(2rem, 6vw, 3.4rem); margin: 0 0 1.5rem; }}
    h2 {{ margin-top: 2.5rem; font-size: 1.65rem; }}
    h3 {{ margin-top: 2rem; font-size: 1.25rem; }}
    p, li {{ font-size: 1.08rem; }}
    a {{ color: var(--accent); text-underline-offset: .16em; }}
    blockquote {{ margin: 2rem 0; padding: .15rem 1.25rem; border-left: 4px solid var(--accent); color: var(--muted); }}
    img {{ max-width: 100%; height: auto; }}
    hr {{ margin: 3rem 0; border: 0; border-top: 1px solid #d9deea; }}
    @media (max-width: 600px) {{ article {{ width: 100%; margin: 0; box-shadow: none; }} }}
    @media print {{ body {{ background: #fff; }} article {{ width: 100%; margin: 0; padding: 0; box-shadow: none; }} }}
  </style>
</head>
<body><article>{rendered_body}</article></body>
</html>
"""

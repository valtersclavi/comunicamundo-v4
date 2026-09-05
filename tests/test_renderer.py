from datetime import UTC, datetime
from uuid import uuid4

from comunicamundo_v4.hefesto.contracts import FinalArticle
import json

from comunicamundo_v4.hefesto.renderer import ArticleRenderer, persist_complete_run_receipt


def _article(body: str) -> FinalArticle:
    return FinalArticle(
        project_id=uuid4(),
        editorial_project_id=uuid4(),
        title="Uma infância atravessada pelo luto",
        body_markdown=body,
        word_count=max(1, len(body.split())),
        quality_score=91,
        revision_rounds=1,
        created_at=datetime(2026, 9, 5, tzinfo=UTC),
    )


def test_renderer_adds_missing_title_once_and_creates_complete_html() -> None:
    markdown, html = ArticleRenderer().render(
        _article("Abertura sensível.\n\n## Como acolher\n\nEscute.")
    )

    assert markdown.startswith("# Uma infância atravessada pelo luto\n\n")
    assert html.count("<h1>") == 1
    assert "<h2>Como acolher</h2>" in html
    assert '<html lang="pt-BR">' in html
    assert "@media print" in html


def test_renderer_preserves_existing_matching_h1() -> None:
    body = "# Uma infância atravessada pelo luto\n\nTexto completo."
    markdown, html = ArticleRenderer().render(_article(body))

    assert markdown.count("# Uma infância atravessada pelo luto") == 1
    assert html.count("<h1>") == 1


def test_renderer_does_not_execute_model_supplied_html() -> None:
    _, html = ArticleRenderer().render(_article("<script>alert('x')</script>"))

    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_complete_run_receipt_is_immutable_and_content_addressed() -> None:
    class Blob:
        value = None

        def upload_from_string(self, value, **kwargs):
            assert kwargs["if_generation_match"] == 0
            self.value = value

        def download_as_bytes(self):
            return self.value

    class Bucket:
        stored = Blob()

        def blob(self, name):
            self.name = name
            return self.stored

    class Storage:
        stored = Bucket()

        def bucket(self, name):
            assert name == "proof-bucket"
            return self.stored

    project_id, execution_run_id = uuid4(), uuid4()
    pointer = persist_complete_run_receipt(
        Storage(),
        bucket_name="proof-bucket",
        root_prefix="root",
        project_id=project_id,
        execution_run_id=execution_run_id,
        payload={"status": "complete", "quality": 92},
    )

    assert pointer.uri.endswith(f"/{project_id}/executions/{execution_run_id}/complete-run.json")
    assert json.loads(Storage.stored.stored.value) == {"quality": 92, "status": "complete"}

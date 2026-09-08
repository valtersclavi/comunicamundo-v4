# ComunicAmundo V4 — Hefesto

Hefesto is a generic, paragraph-first editorial production system. It accepts a brief and one or more PDFs, normalizes and records their source structure without using an LLM, digests each paragraph once, synthesizes the corpus once, and stops at explicit human gates for the Narrative Axis and Editorial Project.

The canonical architecture is documented in `docs/decisions/2026-09-07-hefesto-product-architecture.md`.

## Product input

Every editorial project enters through the same JSON contract. Tests, subjects, and individual PDFs never require product code.

```json
{
  "project_id": "2cf1cd94-51f6-4f78-a98b-c7e04855ad67",
  "brief": {
    "theme": "The subject the user wants to publish",
    "approach": "The user's editorial intention",
    "duration_minutes": 10,
    "output_language": "pt-BR",
    "audience": "The intended audience",
    "product": "article"
  },
  "sources": [
    {
      "uri": "gs://an-upload-bucket/source.pdf",
      "title": "Optional source title",
      "language": "en"
    },
    {
      "uri": "https://public.example/report.pdf",
      "language": "pt-BR"
    }
  ]
}
```

`ingest` downloads each PDF, validates it, stores an immutable original, extracts layout-aware text, creates complete paragraphs and sentence addresses, and persists that structure in PostgreSQL. It makes zero model calls. `plan` performs the same input stage and continues through digestion and the proposed Narrative Axis. `resume-plan` resumes persisted work; `approve-axis` is the explicit action that proposes the Editorial Project.

Every operation emits a durable receipt with state, error details, stage timings, and exact provider usage when model calls occur.

## Editorial API

The authenticated Cloud Run service `hefesto-v4-api` is the product entry point. A client submits the same JSON contract directly; the API persists it immutably, assigns a stable execution identity, dispatches the canonical Job, and returns HTTP 202 without holding an editorial run open.

```http
POST /v1/plans
GET  /v1/plans/{execution_run_id}
POST /v1/plans/{execution_run_id}/resume
POST /v1/plans/{execution_run_id}/approve-axis
GET  /healthz
```

Submitting an identical manifest again returns the existing dispatch instead of starting another paid run. The API remains private under Cloud Run IAM; callers use a Google-signed identity token. It never accepts an unauthenticated request that can trigger model spend.

```bash
comunicamundo-v4-worker ingest --request gs://bucket/project-input.json
comunicamundo-v4-worker plan --request gs://bucket/project-input.json
comunicamundo-v4-worker resume-plan --execution-run-id UUID
comunicamundo-v4-worker approve-axis --execution-run-id UUID
comunicamundo-v4-worker inspect --execution-run-id UUID
```

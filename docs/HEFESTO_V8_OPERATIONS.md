# V8 technical operation

The editorial engine is under `hefesto/v8`. Parsers, authentication, uploads,
storage and PostgreSQL access are V8 infrastructure.

The complete UI is `/` (also available at `/v8`), with login through the existing shared session.
`POST /v8/projects` accepts `{project_id, brief, sources}`. Empty sources means
Hermes first builds a corpus. The UI can upload original files or accept public
URLs, view the short PE and its immutable versions, submit remoque, approve the
current version, produce the work, inspect grounding and resume paused jobs.

Routes under `/v8/projects/{execution_id}`: GET, POST revise (version,message),
approve (version), produce (version), resume. Ownership is checked for all
projects and uploaded GCS sources. API calls enqueue jobs, never generate in the
HTTP request. Worker: `comunicamundo-v8-worker v8-run --execution-run-id=UUID`.
Read-only public receipts exclude full corpus bytes but keep source addresses,
PE/history/work/grounding and telemetry. Full state is in PostgreSQL.

## Configuration and deployment

Required existing configuration: GOOGLE_CLOUD_PROJECT, HEFESTO_POSTGRES_URL,
HEFESTO_POSTGRES_DIRECT_URL for V8 session locks and migrations, HEFESTO_GCS_BUCKET,
HEFESTO_JOB_NAME, GOOGLE_CLOUD_LOCATION for Cloud Run jobs.
V8 generation uses HEFESTO_VERTEX_LOCATION (default global),
HEFESTO_V8_MODEL (default gemini-2.5-flash) and an optional verified
HEFESTO_V8_MODEL_PROFILE. See HEFESTO_V8_PROVIDER.md, including model retirement.
Hermes needs YOU_API_KEY and/or OPENALEX_API_KEY; acquisition may additionally
use JINA_API_KEY and BROWSER_USE_API_KEY as already supported by transports.
Missing search configuration pauses only the path that actually needs search.
Optional HEFESTO_V8_EXTERNAL_PRICES maps search/acquire provider names to USD per
request for the account plan. Without a known tariff, telemetry explicitly marks
external API costs unknown instead of pretending their cost was zero. Token
model costs are priced from the verified profile; infrastructure/storage costs
are not included in this provider-usage estimate.

Bind HEFESTO_POSTGRES_DIRECT_URL to both API and job. V8 uses a direct
PostgreSQL pool because Neon transaction pooling cannot retain session advisory
locks. Authentication and uploads can use the pooled URL. A Neon -pooler host supplied as
the direct URL is rejected explicitly. See https://neon.com/docs/connect/connection-pooling.

Apply the migration directory with `comunicamundo-v8-worker migrate-postgres`.
1008 defines project state and the operation journal. 1009 defines V8 login,
sessions and uploads without replacing existing records. 1010 permanently drops
all explicitly identified V4 tables and their data, without CASCADE. It runs in
one transaction and fails if an unexpected external dependency exists. Existing
V8 projects, journals, login, sessions and uploads remain intact. Fresh databases
need an explicitly provisioned credential; no hard-coded password is installed.

Deploy API and worker from the same commit. The production workflow deploys both
before retiring the database tables, so no old API receives writes afterward.
Remote GCP resource names still identify the same V8 resources; changing those
names requires a separate infrastructure migration, not code compatibility.

## Recovery

Operation identity includes model, prompt version, stage, full prompt/schema and
system instruction. Raw SDK response, text, finish reason and usage are committed
before JSON/semantic parsing. Complete calls are reused; interrupted JSON repairs
use only the response and address index, not the corpus. MAX_TOKENS continuations
append to persisted output. Cache loss never loses project truth.

A source extraction failure, rate limit or timeout pauses the action; it is not
no_reliable_support. Resume keeps the same pending action and completed expensive
operations. Unknown in-flight responses (crash before any response was journaled)
are explicitly surfaced; repeating the unresolved request needs an acknowledged
retry, since the provider cannot promise that the first request was not billed.
No claim of exactly-once external network effects is possible across that gap.
PostgreSQL session advisory locks prevent overlapping project jobs; revision CAS
also protects against stale saves. Locks automatically release on disconnected
sessions. The UI also offers resume while a job was left in an active/queued state
after a process crash; the advisory lock rejects overlap with a still-running
worker. PE and work versions are never silently discarded.

## Technical validation (no model calls)

`python -m pytest`; `python -m compileall -q src`;
`node --check src/comunicamundo_v8/web/assets/v8.js`;
`python -m build --wheel --no-isolation`.
Optional PostgreSQL WASM migration check:
`npm install --no-save @electric-sql/pglite` then
`node scripts/verify_v8_migrations.mjs`.
Tests use generic documents and mocks, including the supplied/zero-source flows,
remoque/research, versioned approval, continuous writing, verification and local
patches, JSON repair, MAX_TOKENS, 429, timeout, restart, cache loss and ownership.

The final editorial paid test is exclusively the user's responsibility. This
technical delivery does not prove editorial quality or live provider availability.

Offline browser smoke: install Playwright/Chromium, then run `node scripts/smoke_v8_ui.cjs`; every HTTP response is a fixture and no model call is made.

Public Cloud Run health endpoint: `/health`; `/healthz` remains a local compatibility alias. Google Frontend reserves some paths ending in z, so public deployment smoke must use `/health`. See https://docs.cloud.google.com/run/docs/known-issues#reserved-url-paths.

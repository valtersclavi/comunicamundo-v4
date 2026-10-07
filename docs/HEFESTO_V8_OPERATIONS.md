# V8 technical operation

V8 is isolated under `hefesto/v8`; legacy parsers, authentication, uploads,
storage and PostgreSQL infrastructure are reused. There is no A/B runner.

The complete UI is `/v8`, with login through the existing shared session.
`POST /v8/projects` accepts `{project_id, brief, sources}`. Empty sources means
Hermes first builds a corpus. The UI can upload original files or accept public
URLs, view the short PE and its immutable versions, submit remoque, approve the
current version, produce the work, inspect grounding and resume paused jobs.

Routes under `/v8/projects/{execution_id}`: GET, POST revise (version,message),
approve (version), produce (version), resume. Ownership is checked for all
projects and uploaded GCS sources. API calls enqueue jobs, never generate in the
HTTP request. Worker: `comunicamundo-v4-worker v8-run --execution-run-id=UUID`.
Read-only public receipts exclude full corpus bytes but keep source addresses,
PE/history/work/grounding and telemetry. Full state is in PostgreSQL.

## Configuration and deployment

Required existing configuration: GOOGLE_CLOUD_PROJECT, HEFESTO_POSTGRES_URL,
HEFESTO_POSTGRES_DIRECT_URL for migrations, HEFESTO_GCS_BUCKET,
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

Apply additive migration 1008 with the existing migration runner. It adds only
v8_projects and v8_operations; it does not migrate or delete legacy project data.
Deploy the API and job from exactly the same V8 commit. The current main workflow
only deploys pushes to main; commits on hefesto-v8 do not publish V8. No production
migration, merge or deploy is implied by a technical commit.

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
sessions. PE and work versions are never silently discarded.

## Technical validation (no model calls)

`python -m pytest`; `python -m compileall -q src`;
`node --check src/comunicamundo_v4/web/assets/v8.js`;
`python -m build --wheel --no-isolation`.
Optional PostgreSQL WASM migration check:
`npm install --no-save @electric-sql/pglite @electric-sql/pglite-pgvector` then
`node scripts/verify_v8_migrations.mjs`.
Tests use generic documents and mocks, including the supplied/zero-source flows,
remoque/research, versioned approval, continuous writing, verification and local
patches, JSON repair, MAX_TOKENS, 429, timeout, restart, cache loss and ownership.

The final editorial paid test is exclusively the user's responsibility. This
technical delivery does not prove editorial quality or live provider availability.

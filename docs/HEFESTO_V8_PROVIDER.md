# V8 provider contract — verified 2026-10-07

The configured model is Gemini 2.5 Flash. Official vendor model
reference reports a 1,048,576 context window and 65,536 maximum output tokens:
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/2-5-flash

The vendor page currently also announces retirement on 2026-10-20. V8 does not
silently choose a replacement. `HEFESTO_V8_MODEL` selects the exact model;
`HEFESTO_V8_MODEL_PROFILE` provides a verified JSON capacity/pricing profile for
other models. Missing profile or retired model blocks generation explicitly.
The default profile is sourced and dated, not based on memory. Deployment must
review model availability before any paid production run.

Required profile keys: input_tokens, output_tokens, source, verified_at,
input_price, cached_price, output_price, price_source, thinking_budget_max,
thinking_source; optional retirement_date.
Prices are USD per million tokens. Flash text prices verified on the vendor page
are 0.30 / 0.03 cached / 2.50 output plus reasoning:
https://cloud.google.com/gemini-enterprise-agent-platform/generative-ai/pricing

Count tokens uses the provider's countTokens endpoint, not a character heuristic
for corpus routing. All calls reserve the configured actual maximum output and
count prompts, schema, instructions and history. No arbitrary corpus margin.
Structured responses are requested as JSON without an SDK parsing schema so raw
candidate output and metadata can be durably journaled before parsing.

Context provider uses mandatory explicit Vertex caches for the reusable material
declared by each editorial operation: addressed sources/corpus, briefing, draft
and PE, according to the operation's existing inputs. Source readings cache their
block and briefing; planning caches its working input; PE synthesis caches briefing
and the persisted draft; writing caches corpus, briefing, draft and PE; reviews
reuse their stable editorial material. Operation instructions/schema stay dynamic.
`aio.caches.create/get/update/list/delete` and
`GenerateContentConfig(cached_content=resource)` are the Google Gen AI SDK APIs.
The cache has no system instruction. Google forbids setting system_instruction
on cached generation requests, so the unchanged operation instructions/schema
are placed in the dynamic contents; no system/tool configuration is duplicated.
Official documentation reverified 2026-10-07:
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-create
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-use
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-update
https://googleapis.github.io/python-genai/

Verified Flash profile: minimum 2,048 tokens; inline text/blob ceiling 10 MB
(conservative decimal MB interpretation), with larger content uploaded unchanged
to a content-identified immutable text/plain object in the existing project GCS
bucket. Conditional creation prevents overwrite; an existing object must match
byte-for-byte. Cache TTL is 3,600 seconds, renewed on reuse; inactivity simply
expires it. Global endpoint is supported. Cache creation input is USD 0.30 per
million tokens; reads USD 0.03 (90% discount); storage USD 1.00 per million
token-hours, according to the pricing source above. Every model must provide its
own verified minimum, inline limit, TTL, storage price and cache source; no
invented profile fallback.

Resource identity includes execution/project ownership, provider, exact model,
provider project, location, corpus manifest, exact cached material identity, slot,
context kind and editorial-context-v2 format. Changes to briefing/draft/PE that
affect the input change this identity; stage instructions do not. A completed
operation remains reusable independently of its former cache. Metadata/events
live in the existing v8_operations journal; no schema
or migration change. Completed editorial operation identity excludes the cache
resource and can resume even if the resource disappeared. Raw output journal
still precedes parsing. Failed eligible cache creation/reuse pauses recoverably,
without inline fallback. A lost create response is reconciled by exact display
identity via caches.list before creation is repeated. Expired/deleted caches are
rebuilt from the exact reusable material, never re-extraction or new editorial
generation. A resource disappearing between lookup and renewal is recreated in
the same preparation. Superseded resources are retired before replacement to keep
at most two active slots, including the transition from source readings to the
merged editorial context. Idle caches
expire without background renewal.

Two-block readings use their respective two slots. After those readings, the
existing combination of grounded readings and necessary original support is
cached with the relevant briefing/material; the reading resources are retired,
so no third active context is introduced. No change to editorial compression.
Small reusable prefixes are grouped with the remaining input already supplied
to that operation. If even that complete input is below the verified provider
minimum, report the real cache constraint; never silently generate uncached or
invent padding. These provider limits were rechecked on 2026-10-12 at the official
overview/create/update pages linked above. Response
packaging repair is deterministic and makes no model call. When a response must
be regenerated, the original operation reuses the same available context cache.
Preflight uses cache metadata token count + counted dynamic contents + full
output reservation, without counting cache tokens twice. Actual provider read
counts, uncached input, output, reasoning and generation costs remain recorded.
Cache creation/reuse/recreation/expiration/deletion and latencies are durable
receipts. API usage adds creations/reuses, reasoning, uncached input, creation
input and storage estimates: accrued receipts through activity, and total lease
estimate through the last expiration (remaining liability included). Catalog-rate
estimates are not a Cloud Billing invoice; generation/creation costs and estimated
storage are shown separately. No UI/product/editorial schema changes.

The output feasibility estimate uses 150 narrated words/minute and 4 tokens per
word including multilingual/tokenization variation and support JSON. This is an
explicit conservative planning estimate, not a silent truncation. Large requests
requiring approved narrative partitions are reported to the user before writing;
V8 does not create independent section writers. Actual word count and duration
are reported and the verifier checks requested duration.

## Maximum thinking for the first V8 editorial test — 2026-10-07

Reverified official Google Cloud thinking documentation:
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/thinking
Gemini 2.5 Flash manual maximum: 24,576 tokens; automatic mode up to 8,192;
zero disables thinking. V8 uses the verified profile maximum on every generation,
with temperature 1.0 (native default verified at the model URL above). topP/topK
are unchanged. No economical or stage-specific budget is used. The maximum is
model-specific, not a universal constant. Missing/invalid maximum or missing
official thinking source pauses configuration before generation; no fallback.

The existing 65,536-token output reservation is retained, leaving room for both
maximum thinking and response. Usage records actual thoughts_token_count as
reasoning_tokens, separately from candidates_token_count/output_tokens; pricing
uses response plus reasoning at the output rate, input minus cached at the input
rate, and cached input at the cached rate. Raw SDK metadata and durable telemetry
are preserved; mocks verify cost and resumed-journal reuse without live calls.
The configured maximum allows that capacity; actual thinking consumption remains
provider-reported and is not forced to equal the maximum. No editorial test or
paid generation is executed by the assistant.

# V8 provider contract — verified 2026-10-07

The current deployed legacy model is Gemini 2.5 Flash. Official vendor model
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

Context provider uses inline text with a stable, addressed corpus prefix and
implicit Vertex caching. No explicit-cache storage charges or cache lifetime
assumptions; cache misses simply send the same persistent corpus. Cached token
usage is captured from provider metadata. The SDK's current cached_content API
was verified but explicit caches are not needed for this initial strategy:
https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/context-cache/context-cache-overview
https://googleapis.github.io/python-genai/index.html

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

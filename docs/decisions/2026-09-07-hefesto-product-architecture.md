# Hefesto V4 product architecture

Status: CANONICAL

This document replaces the graph, dossier, reducer, evidence-matrix, and multi-agent editorial architectures. The implementation must be judged against this contract, not adapted around the previous code.

## Product flow

`PDF references + user brief -> deterministic ingestion -> relational source structure -> paragraph digestion -> editorial understanding -> Narrative Axis -> human approval -> Editorial Project -> human approval -> production`

The authenticated Editorial API is the product front door. It accepts the input contract, persists it immutably, derives an idempotent execution identity, dispatches the long-running canonical Cloud Run Job, and returns immediately. GitHub Actions is deployment and emergency-operation infrastructure; it is not an end-user ingestion mechanism.

There is one generic input contract for every editorial project: a brief plus one or more `gs://` or public `https://` PDF references. Test names, source names, subjects, and file-specific download procedures never belong in product code.

Ingestion is a zero-model product stage. It validates each PDF, preserves an immutable original, extracts layout-aware text, normalizes complete paragraphs, derives sentence addresses, persists the source structure in PostgreSQL, and emits a receipt before any paid call. A PDF without extractable text fails explicitly as requiring OCR; it is never silently treated as an empty source.

The brief is present before digestion. It carries the user's intention, audience, product, output language, and requested duration. It may guide attention and relevance, but it may not change what a source says or authorize unsupported claims.

Requested duration is editorial capacity. It governs selection and depth in the understanding, Axis, and Project. It never truncates source reading or splits a semantic paragraph.

## Units and responsibilities

- Source: immutable normalized input.
- Paragraph: the unit of LLM comprehension.
- Sentence: a relationally stored source address inside a paragraph; it is code-owned and is not generated or selected by the LLM during digestion.
- Parallelism: independent paragraph calls may run concurrently. It changes elapsed time only; there is no multi-paragraph model contract or editorial layer.
- Paragraph note: the sole interpretation produced for a paragraph. It contains one or more distinct observations, free-form tags chosen by the model for each observation, paragraph-level relevance to the brief, and references to source sentences.
- Editorial understanding: one corpus-level synthesis built from paragraph notes. It selects and relates what matters for this specific brief and duration.

## Non-negotiable invariants

1. Every paragraph is interpreted exactly once per digestion run.
2. One successful model call produces the sole note for one paragraph.
3. Paragraph calls may run concurrently, but concurrency cannot create extra interpretations, analyst personas, voting, reducers, or editorial layers.
4. No paragraph is split. Sentence records and all source relations are deterministic and code-owned.
5. The model chooses free-form observations and tags. Code validates identity, order, cardinality, evidence coordinates, persistence, and state; it does not impose a domain ontology.
6. Paragraph results are associated by the code with the paragraph that originated each call, assembled deterministically, and persisted. They are never sent to a reducer.
7. There is exactly one corpus synthesis before the Narrative Axis.
8. There are no Source Dossiers, Reduced Dossiers, Property Graph, Evidence Matrix, dominance analysis, or duplicated Understanding/Matrix payloads in the product path.
9. The Narrative Axis consumes only the brief and the editorial understanding. It is proposed once and then stops for human approval.
10. The Editorial Project consumes the approved Axis, brief, and understanding. It returns: source-supported subjects, optional model-knowledge suggestions, and opportunities requiring additional sources. It stops for human approval.
11. No renderer or production work occurs before Project approval.
12. Every paid call is recorded with provider-reported usage, latency, outcome, and rated cost. Completed work is resumable by deterministic identity.
13. Hashes, UUIDs, ordinals, and source relations are code-owned. The model never has to reproduce identifiers or coordinates.
14. Input normalization and relational source persistence complete before the first model call and report zero model invocations.
15. Test cases are consumers of the public input contract. They cannot add workflows, source lists, or branches to the product implementation.
16. Repeating an identical API submission cannot intentionally create another paid execution; dispatch identity is derived from the complete canonical request.
17. The API is authenticated and asynchronous. It never keeps an HTTP request open for editorial processing and never exposes an unauthenticated endpoint capable of spending model tokens.

## Model contracts

### Paragraph digestion

Input: one complete paragraph plus the full brief.

Output: exactly one structured note:

- `observations` (one or more); each contains:
  - `observation`
  - `tags` (free-form strings)
- `brief_relevance`

One reading may yield several observations; this does not create several analysts or readings. The output has no fixed scientific taxonomy. Empty or content-free paragraphs are handled deterministically and are not sent to the model.

### Editorial understanding

Input: brief plus the ordered compact paragraph notes.

Output:

- integrated synthesis;
- central ideas;
- meaningful relations or tensions;
- nuances and limits when the material contains them;
- editorial possibilities appropriate to the requested product and duration;

This is the only corpus-level interpretation before the Axis.

### Narrative Axis

Input: brief plus editorial understanding.

Output: one 1-2 sentence intellectual/editorial direction and a short rationale. No identifiers, outline, or Editorial Project.

### Editorial Project

Input: approved Axis, brief, and editorial understanding.

Output: working title, editorial promise, source-supported core subjects sized to fill the requested duration with adequate depth, optional model-knowledge suggestions, and source-enrichment opportunities. No article prose.

## Cost and complexity law

Each paragraph is read successfully by the model once. A malformed response may be repeated once. Transient provider errors such as quota pressure may use bounded exponential backoff; zero-token and paid attempts alike appear in the receipt. Completed paragraph notes are never reread on resume. Downstream stages receive compact paragraph notes or the single understanding, never the complete source plus successive verbose rewrites. Increasing concurrency may reduce elapsed time; it cannot change successful semantic cardinality or add layers.

## Acceptance proof

Before a paid multifonte run:

- a real text PDF proves layout extraction and paragraph normalization;
- generic ingress proves immutable source capture and emits a zero-model receipt;
- PostgreSQL structure persistence completes before any model invocation;
- unit tests prove paragraph partition completeness and uniqueness;
- tests prove the brief and duration reach paragraph digestion, understanding, Axis, and Project;
- tests prove free-form tags survive without ontology coercion;
- tests prove concurrency changes neither call count nor semantic output cardinality;
- tests prove exactly one synthesis call and one Axis call occur;
- tests prove the pipeline stops at proposed Axis and proposed Project gates;
- tests prove failure resumes completed paragraph batches without rereading them;
- no-model CI, image build, PostgreSQL migration, and startup smoke pass.

Any acceptance corpus is submitted through the same product contract and must not modify code. A planning operation stops at the proposed Narrative Axis; an explicit human approval action may then produce the proposed Editorial Project. Renderer and production remain blocked until Project approval.

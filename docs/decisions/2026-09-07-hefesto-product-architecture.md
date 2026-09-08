# Hefesto V4 product architecture

Status: CANONICAL

This document replaces the graph, dossier, reducer, evidence-matrix, and multi-agent editorial architectures. The implementation must be judged against this contract, not adapted around the previous code.

## Product flow

`sources + user brief -> paragraph digestion -> editorial understanding -> Narrative Axis -> human approval -> Editorial Project -> human approval -> production`

The brief is present before digestion. It carries the user's intention, audience, product, output language, and requested duration. It may guide attention and relevance, but it may not change what a source says or authorize unsupported claims.

Requested duration is editorial capacity. It governs selection and depth in the understanding, Axis, and Project. It never truncates source reading or splits a semantic paragraph.

## Units and responsibilities

- Source: immutable normalized input.
- Paragraph: the unit of LLM comprehension.
- Sentence: a relationally stored source address inside a paragraph; it is code-owned and is not generated or selected by the LLM during digestion.
- Batch: an operational envelope containing adjacent complete paragraphs. It exists only for concurrency and provider efficiency; it is not an editorial layer.
- Paragraph note: the sole interpretation produced for a paragraph. It contains one or more distinct observations, free-form tags chosen by the model for each observation, paragraph-level relevance to the brief, and references to source sentences.
- Editorial understanding: one corpus-level synthesis built from paragraph notes. It selects and relates what matters for this specific brief and duration.

## Non-negotiable invariants

1. Every paragraph is interpreted exactly once per digestion run.
2. A paragraph belongs to exactly one batch.
3. Batches may run concurrently, but concurrency cannot create extra interpretations, analyst personas, voting, reducers, or editorial layers.
4. No paragraph is split. Sentence records and all source relations are deterministic and code-owned.
5. The model chooses free-form observations and tags. Code validates identity, order, cardinality, evidence coordinates, persistence, and state; it does not impose a domain ontology.
6. Batch results are assembled deterministically and persisted. They are never sent to a reducer.
7. There is exactly one corpus synthesis before the Narrative Axis.
8. There are no Source Dossiers, Reduced Dossiers, Property Graph, Evidence Matrix, dominance analysis, or duplicated Understanding/Matrix payloads in the product path.
9. The Narrative Axis consumes only the brief and the editorial understanding. It is proposed once and then stops for human approval.
10. The Editorial Project consumes the approved Axis, brief, and understanding. It returns: source-supported subjects, optional model-knowledge suggestions, and opportunities requiring additional sources. It stops for human approval.
11. No renderer or production work occurs before Project approval.
12. Every paid call is recorded with provider-reported usage, latency, outcome, and rated cost. Completed work is resumable by deterministic identity.
13. Hashes, UUIDs, ordinals, and source relations are code-owned. The model never has to reproduce identifiers or coordinates.

## Model contracts

### Paragraph batch

Input: complete adjacent paragraphs with paragraph and sentence ordinals, plus the full brief.

Output: exactly one structured note for every input paragraph:

- `observations` (one or more); each contains:
  - `observation`
  - `tags` (free-form strings)
- `brief_relevance`

One reading may yield several observations; this does not create several analysts or readings. The output has no fixed scientific taxonomy. Empty or content-free paragraphs are handled deterministically before batching and are not sent to the model.

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

Source text is read by the model once. Downstream stages receive compact paragraph notes or the single understanding, never the complete source plus successive verbose rewrites. Adding parallel workers may reduce elapsed time; it must not increase the number of semantic layers.

## Acceptance proof

Before a paid multifonte run:

- unit tests prove paragraph partition completeness and uniqueness;
- tests prove the brief and duration reach paragraph digestion, understanding, Axis, and Project;
- tests prove free-form tags survive without ontology coercion;
- tests prove batch count changes concurrency only, not semantic output cardinality;
- tests prove exactly one synthesis call and one Axis call occur;
- tests prove the pipeline stops at proposed Axis and proposed Project gates;
- tests prove failure resumes completed paragraph batches without rereading them;
- no-model CI, image build, PostgreSQL migration, and startup smoke pass.

The first paid proof runs Test A only and stops at the proposed Editorial Project. Test B, renderer, and production remain blocked until the receipt shows a valid understanding, Axis, Project, exact token usage, latency, and rated cost.

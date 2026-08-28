# Decision record — Editorial genericity and the `practical_dimension` mistake

Date: 2026-08-28
Status: canonical starting point for the next Hefesto V4 correction
Repository: `valtersclavi/comunicamundo-v4`
Main before this note: `cd16411339d2cf9e4ade0dab4d7e6b3d3195e900`

## Why this record exists

During validation of the childhood-grief Editorial Project, a user critique that the result lacked practical depth was incorrectly converted into a generic schema/prompt feature named `practical_dimension`.

That was a methodological error.

Hefesto is a generic editorial system. It must not encode one user's critique, one test topic, or one desired output characteristic as a universal editorial field unless the requirement is demonstrated to be generic across products and domains.

The system must work just as coherently for childhood grief, the Iran-Iraq conflict, potatoes in the Andes, an academic analysis, a historical essay, a podcast, a report, or another product. There is no universal "praxis" dimension across all editorial work.

## Original test brief

The childhood-grief test used this brief:

> Produza um blog post sobre como crianças vivenciam o luto, com sensibilidade, clareza e profundidade, usando o artigo como matéria-prima editorial.

Audience:

> Leitores não especialistas interessados em infância, cuidado e elaboração do luto

Product:

> article

This brief does **not** request practical guidance, instructions for adults, action steps, a guide, "how to", or operational recommendations.

Therefore, the absence of praxis in the output was not evidence that the generic Editorial Project contract needed a praxis field. The correct first question should have been: **what in the user's request, source material, editorial understanding, or Narrative Axis would justify practical treatment?**

## The mistake

The critique "there is not enough praxis / the how-to is weak" was treated as a product requirement instead of as a diagnostic observation.

That led to two forms of contamination:

1. `practical_dimension` exists as a field in `EditorialSubjectProposal` and `EditorialSubject`.
2. The Editorial Project prompt explicitly tells the model to generate `practical_dimension` when a subject appears to call for operational practice.
3. Tests were added to assert that `practical_dimension` survives into the generated Editorial Project.

PR #47 did not create the field from nothing — the field already existed in the contract — but PR #47 strengthened the mistake by making it an explicit prompt and test expectation.

## Canonical rule from now on

**Editorial criticism must not be translated directly into schema.**

When a human says an output is missing something, the system design process must first determine **where that characteristic is supposed to originate**:

- user intention / brief;
- product type;
- audience;
- source material;
- editorial understanding;
- Narrative Axis;
- Editorial Project judgment;
- writer/renderer behavior;
- or a genuine generic contract requirement.

Only after locating the missing mechanism should code or schema change.

A test-user preference is not a generic product requirement.

## How practical content should emerge

Practical content is allowed and may be editorially important, but it must emerge contextually.

Examples:

- If a brief asks for "a guide for parents on what to do", practical subjects and concrete actions should naturally become important.
- If the sources themselves contain actionable material and the approved editorial direction makes that material relevant, the editor may select it.
- If a brief asks for an analytical history of the Iran-Iraq conflict, the system must not invent a practical dimension merely because the schema has a place for one.
- If a brief asks about potatoes in the Andes, there is no generic requirement to manufacture praxis.

The generic Editorial Project should represent **editorial choices needed to fulfill the brief under the approved Narrative Axis**, not a fixed checklist of characteristics that happened to be desirable in one test.

## Required correction before the next proof

Remove the `practical_dimension` concept from the generic Editorial Project path without replacing it with a renamed equivalent.

At minimum inspect and correct:

- `EditorialSubjectProposal`
- `EditorialSubject`
- Editorial Project prompt
- builders/orchestrators that copy the field
- fake builders / fixtures
- tests that require or preserve the field
- any renderer or downstream consumer that has started depending on it

Do not add a new field such as `praxis`, `how_to`, `actions`, `operational_guidance`, or another disguised equivalent merely to preserve the same behavior.

Do not modify the Narrative Axis mechanism solely to force practical content into the childhood-grief test.

## Retest protocol after cleanup

The previous PlanningRun and its downstream artifacts are contaminated by the schema/prompt decision and must not be used as evidence for the corrected behavior.

Run a new end-to-end editorial planning proof from the original PDF and original brief.

The proof must create new artifacts rather than reuse the previous ones:

1. original PDF/source input;
2. new normalized source / source lineage;
3. new chunks;
4. new graph extraction;
5. new PropertyGraph;
6. new Editorial Understanding;
7. new Narrative Axis proposal;
8. STOP for human review of the Axis;
9. after explicit human approval/feedback, generate a new Editorial Project;
10. STOP for human review of the Editorial Project.

Do not reuse the previous PlanningRun, PropertyGraph, Editorial Understanding, Narrative Axis, Axis feedback decision, or Editorial Project as shortcuts.

This is intentionally a fresh proof because reusing intermediate artifacts risks stacking errors and makes it impossible to tell which layer produced the behavior.

## What the fresh proof is testing

The fresh proof is **not** "can Hefesto give Valter more praxis?"

It is testing:

- whether the generic source → graph → understanding → Narrative Axis → Editorial Project mechanism behaves coherently from a clean run;
- whether the original brief produces an editorial plan that actually follows what was requested;
- whether practical content appears only when editorially justified rather than because a schema forces it;
- whether the Narrative Axis is the correct mechanism for intellectual direction without becoming a container for every later critique;
- whether the Editorial Project remains compact, subject-oriented, and subordinate to the approved Axis;
- whether human feedback is interpreted as editorial input rather than automatically converted into permanent product ontology.

## Possible later comparative proof

Only after the clean original-brief proof is understood, a useful controlled experiment would be to run the same source with one intentional change to the brief, for example explicitly asking for concrete guidance to adults.

If the system then develops practical material without any dedicated praxis field, that would demonstrate that practical depth is correctly emerging from user intent and editorial judgment.

This comparative test is optional and must not contaminate the first clean proof.

## Non-goals

- Do not tune the system to reproduce a previously liked childhood-grief output.
- Do not optimize for pleasing the test user.
- Do not hard-code grief/adult/child rules.
- Do not require practical treatment across topics.
- Do not treat every criticism as a new schema requirement.
- Do not reuse old intermediate artifacts merely to save execution cost or time when the purpose is to validate the whole mechanism.

## Working principle

**Trace desired editorial behavior back to its legitimate source before changing the system.**

Brief expresses intent. Sources provide material. Understanding interprets the material in light of the request. Narrative Axis chooses the intellectual direction. Editorial Project selects what deserves development. No single test preference should silently become universal ontology.

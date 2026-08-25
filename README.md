# ComunicAmundo V4 — Hefesto clean rebuild

This lineage starts from repository commit `919a5709fbf39a5d5fe6f984087b8cee65461341`, before Hefesto, LlamaIndex, PropertyGraph, persistence, workers or deployment existed.

Canonical product flow:

`brief + normalized sources -> NEW PropertyGraph -> project understanding -> proposed Narrative Axis -> human approval -> Editorial Project -> human approval -> production`

Rules:

- every new plan builds a new project-scoped LlamaIndex `PropertyGraphIndex`;
- the Narrative Axis is 1–2 sentences and is never auto-approved;
- the Editorial Project does not exist until the Axis is approved;
- the Editorial Project is compact: core subjects, model-knowledge subjects, and enrichment opportunities;
- production cannot start until the Editorial Project is independently approved;
- user revisions are natural-language editorial feedback, not field-level patch instructions.

This branch does not copy the previous `graph.py`, `runtime.py`, or approval implementation.

# Hefesto V2 test operator

The canonical manual test path is the GitHub Actions workflow `hefesto-v2-operator`.

## Run

1. Open **Actions → hefesto-v2-operator → Run workflow**.
2. Paste the canonical `gs://` PlanningRequest URI.
3. Keep the internal admission-control ceiling at `1000000` unless the test contract changes it.
4. Start the run.

The operator uses a bounded pool of up to 8 calls globally and 6 calls per project, then runs `digest-v2` → `propose-axis-v2` → `approve-axis-v2`. The last command approves the proposed Axis and creates the proposed Editorial Project. The operator stops at `project_proposed`; it never approves the Editorial Project, produces an article, or starts Test B.

## Output

The workflow summary shows the request, Cloud Run execution names, execution-run identity, terminal state, and Narrative Axis. The `hefesto-v2-operator-result` artifact contains:

- `operator-result.json` with stage timestamps and the proposed Narrative Axis;
- `operator-axis.json` with the complete durable proposed Axis record;
- `operator-project.json` with the complete durable proposed Editorial Project record;
- `operator-failure.json` with durable state when a stage fails.

The temporary read-only state service runs with the canonical runtime identity and is deleted by an `always()` cleanup step. Starting a test requires no workflow edit, trigger commit, job mutation, image build, or deployment.

## Evidence quotation contract

The model cites immutable section, paragraph, and first/last sentence IDs; it never writes the excerpt. Hefesto validates that the IDs select complete, consecutive sentences inside one normalized paragraph and reconstructs the exact excerpt in code. There is no quotation-length ceiling. A second paragraph requires another anchor.\n\n## Semantic planning and capacity\n\nParagraphs are indivisible source-native units. Headings define preferred message blocks; unheaded prose uses paragraph boundaries. Source size controls the number of pooled analysts (24,000 estimated source tokens per analyst, capped at 8), but never creates a cut inside a paragraph. The separate 200,000-token hard section ceiling is provider admission safety only and can add a boundary only between complete paragraphs.\n\nThe default execution pool is 8 globally and 6 per project. The token bucket remains the final admission control, so configured concurrency is a ceiling rather than a promise that every slot starts at once. Source dossiers have a 12,000-token output budget; incomplete provider responses, including `MAX_TOKENS` and invalid structured JSON, are recorded as paid terminal failures and are never retried silently.

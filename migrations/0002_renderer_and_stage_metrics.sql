CREATE TABLE IF NOT EXISTS hefesto.stage_events (
    stage_event_id uuid PRIMARY KEY,
    execution_run_id uuid NOT NULL REFERENCES hefesto.digestion_runs(execution_run_id) ON DELETE RESTRICT,
    project_id uuid NOT NULL,
    stage text NOT NULL,
    status text NOT NULL CHECK (status IN ('succeeded', 'failed')),
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    duration_ms bigint NOT NULL CHECK (duration_ms >= 0),
    details jsonb NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (execution_run_id, stage, started_at)
);
CREATE INDEX IF NOT EXISTS stage_events_run_idx
    ON hefesto.stage_events (execution_run_id, started_at);

CREATE TABLE IF NOT EXISTS hefesto.rendered_products (
    rendered_product_id uuid PRIMARY KEY,
    execution_run_id uuid NOT NULL REFERENCES hefesto.production_runs(execution_run_id) ON DELETE RESTRICT,
    project_id uuid NOT NULL,
    editorial_project_id uuid NOT NULL,
    product_type text NOT NULL,
    markdown_uri text NOT NULL CHECK (markdown_uri LIKE 'gs://%'),
    markdown_sha256 char(64) NOT NULL,
    html_uri text NOT NULL CHECK (html_uri LIKE 'gs://%'),
    html_sha256 char(64) NOT NULL,
    manifest_uri text NOT NULL CHECK (manifest_uri LIKE 'gs://%'),
    manifest_sha256 char(64) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL,
    UNIQUE (execution_run_id, product_type)
);

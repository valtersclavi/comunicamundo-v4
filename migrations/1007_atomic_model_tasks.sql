-- Pre-PE tasks also checkpoint before a run row exists, hence no runs FK.
CREATE TABLE IF NOT EXISTS hefesto_product.atomic_model_tasks (
    execution_run_id uuid NOT NULL,
    task_key char(64) NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, task_key)
);

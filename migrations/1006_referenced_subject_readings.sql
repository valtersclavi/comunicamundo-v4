-- Direct editorial readings reference immutable relational originals.
-- Legacy condensations remain readable; they are not input to the new path.
CREATE TABLE IF NOT EXISTS hefesto_product.subject_readings (
    execution_run_id uuid NOT NULL REFERENCES hefesto_product.runs(execution_run_id),
    reading_key char(64) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, reading_key)
);

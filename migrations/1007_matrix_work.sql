-- Checkpoints for the new matrix planning and subject-by-subject writing path.
CREATE TABLE IF NOT EXISTS hefesto_product.editorial_matrix_work (
    execution_run_id uuid NOT NULL REFERENCES hefesto_product.runs(execution_run_id),
    round_number integer NOT NULL CHECK (round_number > 0),
    task text NOT NULL,
    unit_id char(64) NOT NULL,
    input_digest char(64) NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, round_number, task, unit_id)
);
CREATE INDEX IF NOT EXISTS editorial_matrix_work_order_idx
    ON hefesto_product.editorial_matrix_work(execution_run_id,round_number,task,created_at);

-- Additive V8 state and durable provider journal. No legacy rows are modified.
CREATE TABLE IF NOT EXISTS hefesto_product.v8_projects (
    execution_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    revision bigint NOT NULL DEFAULT 0,
    status text NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS v8_projects_owner_updated_idx
    ON hefesto_product.v8_projects(owner_id, updated_at DESC);
CREATE TABLE IF NOT EXISTS hefesto_product.v8_operations (
    execution_id uuid NOT NULL REFERENCES hefesto_product.v8_projects(execution_id),
    operation_key char(64) NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_id, operation_key)
);

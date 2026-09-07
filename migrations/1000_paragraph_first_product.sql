CREATE SCHEMA IF NOT EXISTS hefesto_product;

CREATE TABLE IF NOT EXISTS hefesto_product.schema_migrations (
    version text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hefesto_product.runs (
    execution_run_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    state text NOT NULL,
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hefesto_product.paragraphs (
    execution_run_id uuid NOT NULL REFERENCES hefesto_product.runs(execution_run_id),
    paragraph_id char(64) NOT NULL,
    source_id uuid NOT NULL,
    source_ordinal integer NOT NULL,
    paragraph_ordinal integer NOT NULL,
    text text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (execution_run_id, paragraph_id),
    UNIQUE (execution_run_id, source_ordinal, paragraph_ordinal)
);

CREATE TABLE IF NOT EXISTS hefesto_product.sentences (
    execution_run_id uuid NOT NULL,
    paragraph_id char(64) NOT NULL,
    sentence_id char(64) NOT NULL,
    ordinal integer NOT NULL,
    text text NOT NULL,
    PRIMARY KEY (execution_run_id, sentence_id),
    UNIQUE (execution_run_id, paragraph_id, ordinal),
    FOREIGN KEY (execution_run_id, paragraph_id)
        REFERENCES hefesto_product.paragraphs(execution_run_id, paragraph_id)
);

CREATE TABLE IF NOT EXISTS hefesto_product.paragraph_notes (
    execution_run_id uuid NOT NULL,
    paragraph_id char(64) NOT NULL,
    source_id uuid NOT NULL,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, paragraph_id),
    FOREIGN KEY (execution_run_id, paragraph_id)
        REFERENCES hefesto_product.paragraphs(execution_run_id, paragraph_id)
);

CREATE TABLE IF NOT EXISTS hefesto_product.call_usage (
    id bigserial PRIMARY KEY,
    execution_run_id uuid NOT NULL REFERENCES hefesto_product.runs(execution_run_id),
    project_id uuid NOT NULL,
    task_type text NOT NULL,
    status text NOT NULL,
    prompt_tokens bigint NOT NULL,
    cached_tokens bigint NOT NULL,
    output_tokens bigint NOT NULL,
    reasoning_tokens bigint NOT NULL,
    total_tokens bigint NOT NULL,
    latency_ms bigint NOT NULL,
    rated_cost_usd numeric(20,9),
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS call_usage_run_idx
    ON hefesto_product.call_usage(execution_run_id, id);


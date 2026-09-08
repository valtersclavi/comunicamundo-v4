CREATE TABLE IF NOT EXISTS hefesto_product.submissions (
    execution_run_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    owner_id text NOT NULL,
    request_payload jsonb NOT NULL,
    receipt_payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS submissions_owner_created_idx
    ON hefesto_product.submissions(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hefesto_product.uploads (
    upload_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    object_uri text NOT NULL UNIQUE,
    file_name text NOT NULL,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 1073741824),
    upload_session_url text,
    status text NOT NULL CHECK (status IN ('uploading', 'stored', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS uploads_owner_created_idx
    ON hefesto_product.uploads(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hefesto_product.stage_receipts (
    execution_run_id uuid NOT NULL,
    stage text NOT NULL,
    status text NOT NULL,
    duration_ms bigint NOT NULL CHECK (duration_ms >= 0),
    payload jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, stage)
);

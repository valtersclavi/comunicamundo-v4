-- Infrastructure owned by V8. Existing login, sessions and uploads are preserved.
CREATE TABLE IF NOT EXISTS hefesto_product.uploads (
    upload_id uuid PRIMARY KEY,
    owner_id text NOT NULL,
    object_uri text NOT NULL UNIQUE,
    file_name text NOT NULL,
    content_type text NOT NULL,
    size_bytes bigint NOT NULL CHECK (size_bytes > 0 AND size_bytes <= 209715200),
    upload_session_url text,
    status text NOT NULL CHECK (status IN ('uploading', 'stored', 'failed')),
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS uploads_owner_created_idx
    ON hefesto_product.uploads(owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hefesto_product.app_credential (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    username text NOT NULL UNIQUE,
    password_salt bytea NOT NULL,
    password_hash bytea NOT NULL,
    failed_attempts integer NOT NULL DEFAULT 0,
    locked_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hefesto_product.app_sessions (
    token_hash char(64) PRIMARY KEY,
    owner_id text NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS app_sessions_expiry_idx
    ON hefesto_product.app_sessions(expires_at);

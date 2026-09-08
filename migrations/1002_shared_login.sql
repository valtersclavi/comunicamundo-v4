CREATE TABLE IF NOT EXISTS hefesto_product.app_credential (
    singleton boolean PRIMARY KEY DEFAULT true CHECK (singleton),
    username text NOT NULL UNIQUE,
    password_salt bytea NOT NULL,
    password_hash bytea NOT NULL,
    failed_attempts integer NOT NULL DEFAULT 0,
    locked_until timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO hefesto_product.app_credential
    (singleton, username, password_salt, password_hash)
VALUES (
    true,
    'hefesto',
    decode('c4cf0a580474c16e193ee0a9a3ef41cb', 'hex'),
    decode('5666b9667738ce4c769adac70ea648090188cc70d19916b37f7ee2ea00b09a27', 'hex')
)
ON CONFLICT (singleton) DO NOTHING;

CREATE TABLE IF NOT EXISTS hefesto_product.app_sessions (
    token_hash char(64) PRIMARY KEY,
    owner_id text NOT NULL,
    expires_at timestamptz NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS app_sessions_expiry_idx
    ON hefesto_product.app_sessions(expires_at);

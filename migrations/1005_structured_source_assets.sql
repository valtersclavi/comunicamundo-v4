ALTER TABLE hefesto_product.uploads
    DROP CONSTRAINT IF EXISTS uploads_size_bytes_check;

ALTER TABLE hefesto_product.uploads
    ADD CONSTRAINT uploads_size_bytes_check
    CHECK (size_bytes > 0 AND size_bytes <= 209715200);

CREATE TABLE IF NOT EXISTS hefesto_product.source_assets (
    execution_run_id uuid NOT NULL
        REFERENCES hefesto_product.runs(execution_run_id),
    asset_id char(64) NOT NULL,
    source_id uuid NOT NULL,
    source_ordinal integer NOT NULL CHECK (source_ordinal > 0),
    asset_ordinal integer NOT NULL CHECK (asset_ordinal > 0),
    kind text NOT NULL
        CHECK (kind IN ('table', 'figure', 'chart', 'unknown_picture')),
    role text NOT NULL
        CHECK (role IN ('content', 'decorative', 'unknown')),
    page_no integer CHECK (page_no > 0),
    caption text,
    text_representation text,
    blob_uri text,
    structured_uri text,
    binary_sha256 char(64),
    payload jsonb NOT NULL,
    PRIMARY KEY (execution_run_id, asset_id),
    UNIQUE (execution_run_id, source_ordinal, asset_ordinal)
);

CREATE INDEX IF NOT EXISTS source_assets_source_idx
    ON hefesto_product.source_assets(execution_run_id, source_id, asset_ordinal);

CREATE TABLE IF NOT EXISTS hefesto_product.paragraph_assets (
    execution_run_id uuid NOT NULL,
    paragraph_id char(64) NOT NULL,
    asset_id char(64) NOT NULL,
    relation text NOT NULL
        CHECK (relation IN (
            'anchor_after', 'anchor_before', 'caption',
            'context_before', 'context_after'
        )),
    distance integer NOT NULL CHECK (distance >= 0),
    is_primary boolean NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (execution_run_id, paragraph_id, asset_id, relation),
    FOREIGN KEY (execution_run_id, paragraph_id)
        REFERENCES hefesto_product.paragraphs(execution_run_id, paragraph_id),
    FOREIGN KEY (execution_run_id, asset_id)
        REFERENCES hefesto_product.source_assets(execution_run_id, asset_id)
);

CREATE INDEX IF NOT EXISTS paragraph_assets_asset_idx
    ON hefesto_product.paragraph_assets(execution_run_id, asset_id);

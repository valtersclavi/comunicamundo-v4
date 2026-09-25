-- Editorial matrix v1. No changes to source ingestion or segmentation.
CREATE TABLE IF NOT EXISTS hefesto_product.editorial_matrix_batches (
    execution_run_id uuid NOT NULL REFERENCES hefesto_product.runs(execution_run_id),
    batch_id char(64) NOT NULL,
    extraction_version text NOT NULL,
    status text NOT NULL CHECK (status IN ('completed', 'failed')),
    input_tokens integer NOT NULL CHECK (input_tokens > 0),
    error_message text,
    created_at timestamptz NOT NULL DEFAULT now(),
    updated_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, batch_id)
);

CREATE TABLE IF NOT EXISTS hefesto_product.editorial_matrix_coverage (
    execution_run_id uuid NOT NULL,
    batch_id char(64) NOT NULL,
    passage_id char(64) NOT NULL,
    paragraph_id char(64) NOT NULL,
    source_id uuid NOT NULL,
    source_ordinal integer NOT NULL CHECK (source_ordinal > 0),
    examined boolean NOT NULL DEFAULT true,
    PRIMARY KEY (execution_run_id, batch_id, passage_id),
    FOREIGN KEY (execution_run_id, batch_id)
        REFERENCES hefesto_product.editorial_matrix_batches(execution_run_id, batch_id),
    FOREIGN KEY (execution_run_id, paragraph_id)
        REFERENCES hefesto_product.paragraphs(execution_run_id, paragraph_id)
);

CREATE TABLE IF NOT EXISTS hefesto_product.editorial_claims (
    execution_run_id uuid NOT NULL,
    claim_id char(64) NOT NULL,
    batch_id char(64),
    round_number integer NOT NULL DEFAULT 1 CHECK (round_number > 0),
    origin text NOT NULL CHECK (origin IN ('source', 'model')),
    kind text NOT NULL CHECK (kind IN ('idea', 'datum', 'qualification', 'limit', 'contradiction')),
    statement text NOT NULL CHECK (length(trim(statement)) > 0),
    status text NOT NULL DEFAULT 'active' CHECK (status IN ('active', 'superseded')),
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, claim_id),
    FOREIGN KEY (execution_run_id, batch_id)
        REFERENCES hefesto_product.editorial_matrix_batches(execution_run_id, batch_id),
    CHECK (origin = 'model' OR batch_id IS NOT NULL)
);

CREATE TABLE IF NOT EXISTS hefesto_product.editorial_claim_evidence (
    execution_run_id uuid NOT NULL,
    claim_id char(64) NOT NULL,
    passage_id char(64) NOT NULL,
    paragraph_id char(64) NOT NULL,
    source_id uuid NOT NULL,
    source_ordinal integer NOT NULL CHECK (source_ordinal > 0),
    role text NOT NULL CHECK (role IN ('supports', 'limits', 'contradicts')),
    PRIMARY KEY (execution_run_id, claim_id, passage_id, role),
    FOREIGN KEY (execution_run_id, claim_id)
        REFERENCES hefesto_product.editorial_claims(execution_run_id, claim_id),
    FOREIGN KEY (execution_run_id, paragraph_id)
        REFERENCES hefesto_product.paragraphs(execution_run_id, paragraph_id)
);

CREATE TABLE IF NOT EXISTS hefesto_product.editorial_claim_relations (
    execution_run_id uuid NOT NULL,
    left_claim_id char(64) NOT NULL,
    right_claim_id char(64) NOT NULL,
    relation text NOT NULL CHECK (relation IN ('converges', 'complements', 'diverges')),
    rationale text NOT NULL,
    PRIMARY KEY (execution_run_id, left_claim_id, right_claim_id, relation),
    FOREIGN KEY (execution_run_id, left_claim_id)
        REFERENCES hefesto_product.editorial_claims(execution_run_id, claim_id),
    FOREIGN KEY (execution_run_id, right_claim_id)
        REFERENCES hefesto_product.editorial_claims(execution_run_id, claim_id),
    CHECK (left_claim_id <> right_claim_id)
);

CREATE TABLE IF NOT EXISTS hefesto_product.editorial_subject_claims (
    execution_run_id uuid NOT NULL,
    round_number integer NOT NULL CHECK (round_number > 0),
    subject_index integer NOT NULL CHECK (subject_index >= 0),
    claim_id char(64) NOT NULL,
    role text NOT NULL CHECK (role IN ('core', 'context', 'tension', 'qualification')),
    PRIMARY KEY (execution_run_id, round_number, subject_index, claim_id),
    FOREIGN KEY (execution_run_id, claim_id)
        REFERENCES hefesto_product.editorial_claims(execution_run_id, claim_id)
);

CREATE INDEX IF NOT EXISTS editorial_claims_batch_idx
    ON hefesto_product.editorial_claims(execution_run_id, batch_id);
CREATE INDEX IF NOT EXISTS editorial_claim_evidence_source_idx
    ON hefesto_product.editorial_claim_evidence(execution_run_id, source_id);
CREATE INDEX IF NOT EXISTS editorial_subject_claims_subject_idx
    ON hefesto_product.editorial_subject_claims(execution_run_id, round_number, subject_index);

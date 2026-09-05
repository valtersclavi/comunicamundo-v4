CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS hefesto;

CREATE TABLE IF NOT EXISTS hefesto.schema_migrations (
    version text PRIMARY KEY,
    checksum char(64) NOT NULL,
    applied_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS hefesto.digestion_runs (
    execution_run_id uuid PRIMARY KEY,
    project_id uuid NOT NULL,
    state text NOT NULL,
    payload jsonb NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);
CREATE INDEX IF NOT EXISTS digestion_runs_project_idx
    ON hefesto.digestion_runs (project_id, created_at DESC);

CREATE TABLE IF NOT EXISTS hefesto.editorial_runs (
    execution_run_id uuid PRIMARY KEY REFERENCES hefesto.digestion_runs(execution_run_id) ON DELETE RESTRICT,
    project_id uuid NOT NULL,
    state text NOT NULL,
    payload jsonb NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS hefesto.production_runs (
    execution_run_id uuid PRIMARY KEY REFERENCES hefesto.digestion_runs(execution_run_id) ON DELETE RESTRICT,
    project_id uuid NOT NULL,
    editorial_project_id uuid NOT NULL,
    state text NOT NULL,
    payload jsonb NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS hefesto.work_units (
    execution_run_id uuid NOT NULL REFERENCES hefesto.digestion_runs(execution_run_id) ON DELETE RESTRICT,
    work_unit_id char(64) NOT NULL,
    project_id uuid NOT NULL,
    unit_type text NOT NULL,
    status text NOT NULL,
    attempt_count integer NOT NULL CHECK (attempt_count >= 0),
    payload jsonb NOT NULL,
    revision bigint NOT NULL DEFAULT 1 CHECK (revision > 0),
    created_at timestamptz NOT NULL,
    updated_at timestamptz NOT NULL,
    PRIMARY KEY (execution_run_id, work_unit_id)
);
CREATE INDEX IF NOT EXISTS work_units_state_idx
    ON hefesto.work_units (execution_run_id, status, unit_type);

CREATE TABLE IF NOT EXISTS hefesto.llm_attempts (
    attempt_id uuid PRIMARY KEY,
    call_id uuid NOT NULL,
    execution_run_id uuid NOT NULL,
    project_id uuid NOT NULL,
    work_unit_id char(64) NOT NULL,
    task_type text NOT NULL,
    model text NOT NULL,
    status text NOT NULL,
    attempt integer NOT NULL CHECK (attempt > 0),
    queued_at timestamptz NOT NULL,
    started_at timestamptz NOT NULL,
    finished_at timestamptz NOT NULL,
    queue_wait_ms bigint NOT NULL CHECK (queue_wait_ms >= 0),
    latency_ms bigint NOT NULL CHECK (latency_ms >= 0),
    prompt_tokens bigint,
    cached_tokens bigint,
    output_tokens bigint,
    reasoning_tokens bigint,
    total_tokens bigint,
    provider_reported boolean,
    rated_cost_usd numeric(20, 9),
    payload jsonb NOT NULL,
    FOREIGN KEY (execution_run_id, work_unit_id)
        REFERENCES hefesto.work_units(execution_run_id, work_unit_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS llm_attempts_project_time_idx
    ON hefesto.llm_attempts (project_id, started_at);
CREATE INDEX IF NOT EXISTS llm_attempts_run_task_idx
    ON hefesto.llm_attempts (execution_run_id, task_type, attempt);

CREATE TABLE IF NOT EXISTS hefesto.sources (
    execution_run_id uuid NOT NULL REFERENCES hefesto.digestion_runs(execution_run_id) ON DELETE RESTRICT,
    source_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_ordinal integer NOT NULL CHECK (source_ordinal >= 0),
    title text,
    language text NOT NULL,
    content_sha256 char(64) NOT NULL,
    char_count bigint NOT NULL CHECK (char_count > 0),
    estimated_tokens bigint NOT NULL CHECK (estimated_tokens > 0),
    normalized_text text NOT NULL,
    metadata jsonb NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (execution_run_id, source_id),
    UNIQUE (execution_run_id, source_ordinal),
    UNIQUE (execution_run_id, source_id, content_sha256)
);

CREATE TABLE IF NOT EXISTS hefesto.source_sections (
    execution_run_id uuid NOT NULL,
    source_id uuid NOT NULL,
    section_id char(64) NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    char_start bigint NOT NULL CHECK (char_start >= 0),
    char_end bigint NOT NULL CHECK (char_end > char_start),
    content_sha256 char(64) NOT NULL,
    estimated_tokens bigint NOT NULL CHECK (estimated_tokens > 0),
    heading_hint text,
    PRIMARY KEY (execution_run_id, section_id),
    UNIQUE (execution_run_id, source_id, ordinal),
    FOREIGN KEY (execution_run_id, source_id)
        REFERENCES hefesto.sources(execution_run_id, source_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS hefesto.source_paragraphs (
    execution_run_id uuid NOT NULL,
    source_id uuid NOT NULL,
    section_id char(64) NOT NULL,
    paragraph_id char(64) NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    char_start bigint NOT NULL CHECK (char_start >= 0),
    char_end bigint NOT NULL CHECK (char_end > char_start),
    content_sha256 char(64) NOT NULL,
    text text NOT NULL,
    PRIMARY KEY (execution_run_id, paragraph_id),
    UNIQUE (execution_run_id, section_id, ordinal),
    FOREIGN KEY (execution_run_id, section_id)
        REFERENCES hefesto.source_sections(execution_run_id, section_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS hefesto.source_sentences (
    execution_run_id uuid NOT NULL,
    source_id uuid NOT NULL,
    paragraph_id char(64) NOT NULL,
    sentence_id char(64) NOT NULL,
    ordinal integer NOT NULL CHECK (ordinal >= 0),
    char_start bigint NOT NULL CHECK (char_start >= 0),
    char_end bigint NOT NULL CHECK (char_end > char_start),
    content_sha256 char(64) NOT NULL,
    text text NOT NULL,
    PRIMARY KEY (execution_run_id, sentence_id),
    UNIQUE (execution_run_id, paragraph_id, ordinal),
    UNIQUE (execution_run_id, paragraph_id, sentence_id),
    FOREIGN KEY (execution_run_id, paragraph_id)
        REFERENCES hefesto.source_paragraphs(execution_run_id, paragraph_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS hefesto.source_embeddings (
    execution_run_id uuid NOT NULL,
    source_id uuid NOT NULL,
    entity_kind text NOT NULL CHECK (entity_kind IN ('source', 'section', 'paragraph', 'sentence', 'finding')),
    entity_id text NOT NULL,
    model text NOT NULL,
    dimensions integer NOT NULL CHECK (dimensions > 0),
    embedding vector NOT NULL,
    content_sha256 char(64) NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, entity_kind, entity_id, model),
    FOREIGN KEY (execution_run_id, source_id)
        REFERENCES hefesto.sources(execution_run_id, source_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS hefesto.source_findings (
    finding_id char(64) PRIMARY KEY,
    execution_run_id uuid NOT NULL,
    project_id uuid NOT NULL,
    source_id uuid NOT NULL,
    dossier_id uuid,
    section_id char(64),
    finding text NOT NULL,
    causal_status text NOT NULL,
    evidence_strength text NOT NULL,
    qualifications jsonb NOT NULL DEFAULT '[]'::jsonb,
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    FOREIGN KEY (execution_run_id, source_id)
        REFERENCES hefesto.sources(execution_run_id, source_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS hefesto.evidence_anchors (
    finding_id char(64) NOT NULL REFERENCES hefesto.source_findings(finding_id) ON DELETE RESTRICT,
    anchor_ordinal integer NOT NULL CHECK (anchor_ordinal >= 0),
    execution_run_id uuid NOT NULL,
    paragraph_id char(64) NOT NULL,
    first_sentence_id char(64) NOT NULL,
    last_sentence_id char(64) NOT NULL,
    locator text NOT NULL,
    excerpt text NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (finding_id, anchor_ordinal),
    FOREIGN KEY (execution_run_id, paragraph_id, first_sentence_id)
        REFERENCES hefesto.source_sentences(execution_run_id, paragraph_id, sentence_id) ON DELETE RESTRICT,
    FOREIGN KEY (execution_run_id, paragraph_id, last_sentence_id)
        REFERENCES hefesto.source_sentences(execution_run_id, paragraph_id, sentence_id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS hefesto.artifacts (
    artifact_id bigserial PRIMARY KEY,
    execution_run_id uuid NOT NULL REFERENCES hefesto.digestion_runs(execution_run_id) ON DELETE RESTRICT,
    project_id uuid NOT NULL,
    stage text NOT NULL,
    artifact_kind text NOT NULL,
    uri text NOT NULL CHECK (uri LIKE 'gs://%'),
    content_sha256 char(64) NOT NULL,
    payload jsonb,
    created_at timestamptz NOT NULL DEFAULT now(),
    UNIQUE (execution_run_id, stage, artifact_kind, uri)
);


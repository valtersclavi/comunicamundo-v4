CREATE TABLE IF NOT EXISTS hefesto_product.selection_batches (
    execution_run_id uuid NOT NULL
        REFERENCES hefesto_product.runs(execution_run_id),
    batch_id char(64) NOT NULL,
    source_id uuid NOT NULL,
    source_ordinal integer NOT NULL,
    first_passage_ordinal integer NOT NULL,
    last_passage_ordinal integer NOT NULL,
    input_tokens integer NOT NULL CHECK (input_tokens > 0),
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, batch_id)
);

CREATE INDEX IF NOT EXISTS selection_batches_order_idx
    ON hefesto_product.selection_batches(
        execution_run_id, source_ordinal, first_passage_ordinal
    );

CREATE TABLE IF NOT EXISTS hefesto_product.editorial_condensations (
    execution_run_id uuid NOT NULL
        REFERENCES hefesto_product.runs(execution_run_id),
    unit_id char(64) NOT NULL,
    level integer NOT NULL CHECK (level > 0),
    ordinal integer NOT NULL CHECK (ordinal > 0),
    input_tokens integer NOT NULL CHECK (input_tokens > 0),
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (execution_run_id, unit_id)
);

CREATE INDEX IF NOT EXISTS editorial_condensations_order_idx
    ON hefesto_product.editorial_condensations(execution_run_id, level, ordinal);

UPDATE hefesto_product.runs
SET payload = jsonb_set(
        payload - 'understanding' - 'axis',
        '{editorial_project}',
        jsonb_set(
            (payload -> 'editorial_project') - 'narrative_axis_id',
            '{proposal,narrative_axis}',
            payload #> '{axis,proposal}',
            true
        ),
        true
    ),
    updated_at = now()
WHERE payload #>> '{editorial_project,proposal,working_title}' IS NOT NULL
  AND payload #>> '{axis,proposal,statement}' IS NOT NULL;

UPDATE hefesto_product.runs
SET state = 'failed',
    payload = jsonb_set(
        jsonb_set(
            jsonb_set(
                (payload - 'understanding' - 'axis') ||
                    jsonb_build_object('editorial_project', NULL),
                '{state}',
                '"failed"'::jsonb
            ),
            '{error_class}',
            '"SupersededExecution"'::jsonb
        ),
        '{error_message}',
        '"Esta execução incompleta foi encerrada quando a triagem por flags substituiu a digestão por parágrafo. Inicie um novo planejamento."'::jsonb
    ),
    updated_at = now()
WHERE payload #>> '{editorial_project,proposal,narrative_axis,statement}' IS NULL;

DROP TABLE IF EXISTS hefesto_product.paragraph_notes;

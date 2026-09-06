ALTER TABLE hefesto.source_findings
    ALTER COLUMN causal_status DROP NOT NULL,
    ALTER COLUMN evidence_strength DROP NOT NULL;

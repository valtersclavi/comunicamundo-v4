-- Explicitly retired tables and their data. RESTRICT protects unrelated objects.
DROP TABLE IF EXISTS hefesto_product.paragraph_assets;
DROP TABLE IF EXISTS hefesto_product.source_assets;
DROP TABLE IF EXISTS hefesto_product.sentences;
DROP TABLE IF EXISTS hefesto_product.paragraph_notes;
DROP TABLE IF EXISTS hefesto_product.paragraphs;
DROP TABLE IF EXISTS hefesto_product.passage_embeddings;
DROP TABLE IF EXISTS hefesto_product.editorial_condensations;
DROP TABLE IF EXISTS hefesto_product.selection_batches;
DROP TABLE IF EXISTS hefesto_product.subject_readings;
DROP TABLE IF EXISTS hefesto_product.atomic_model_tasks;
DROP TABLE IF EXISTS hefesto_product.call_usage;
DROP TABLE IF EXISTS hefesto_product.stage_receipts;
DROP TABLE IF EXISTS hefesto_product.submissions;
DROP TABLE IF EXISTS hefesto_product.runs;

DELETE FROM hefesto_product.schema_migrations WHERE version IN (
    '999_vector_retrieval.sql',
    '1000_paragraph_first_product.sql',
    '1001_product_access_and_observability.sql',
    '1002_shared_login.sql',
    '1003_selection_and_single_approval.sql',
    '1004_rotate_shared_login.sql',
    '1005_structured_source_assets.sql',
    '1006_referenced_subject_readings.sql',
    '1007_atomic_model_tasks.sql');

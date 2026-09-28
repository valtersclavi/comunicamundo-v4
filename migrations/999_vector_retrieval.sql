CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS hefesto_product.passage_embeddings (
  execution_run_id uuid NOT NULL,
  passage_id char(64) NOT NULL,
  source_ordinal integer NOT NULL CHECK (source_ordinal > 0),
  passage_ordinal integer NOT NULL CHECK (passage_ordinal > 0),
  embedding vector(768) NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (execution_run_id, passage_id)
);

CREATE INDEX IF NOT EXISTS passage_embeddings_execution_vector_idx
  ON hefesto_product.passage_embeddings
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE INDEX IF NOT EXISTS passage_embeddings_execution_position_idx
  ON hefesto_product.passage_embeddings
  (execution_run_id, source_ordinal, passage_ordinal);
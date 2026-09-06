import json
from pathlib import Path

from comunicamundo_v4.hefesto.digestion_contracts import SourceDossierProposal
from comunicamundo_v4.hefesto.llm_gateway import (
    MAX_PROVIDER_SCHEMA_NODES,
    _provider_response_schema,
    _schema_node_count,
    bind_json_output_contract,
)
from comunicamundo_v4.hefesto.postgres import MIGRATIONS_DIR


def test_foundation_migration_has_relational_evidence_chain_and_pgvector() -> None:
    sql = (MIGRATIONS_DIR / "0001_postgres_foundation.sql").read_text()

    assert "CREATE EXTENSION IF NOT EXISTS vector" in sql
    assert "hefesto.source_sections" in sql
    assert "hefesto.source_paragraphs" in sql
    assert "hefesto.source_sentences" in sql
    assert "hefesto.source_findings" in sql
    assert "hefesto.evidence_anchors" in sql
    assert "first_sentence_id" in sql and "last_sentence_id" in sql
    assert "REFERENCES hefesto.source_sentences" in sql


def test_flat_dossier_schema_fits_provider_serving_grammar() -> None:
    schema = _provider_response_schema(SourceDossierProposal)

    assert _schema_node_count(schema) <= MAX_PROVIDER_SCHEMA_NODES


def test_full_output_contract_is_bound_to_model_prompt() -> None:
    prompt = bind_json_output_contract("Read the source.", SourceDossierProposal)
    marker = "OUTPUT JSON CONTRACT (return only one JSON object):\n"
    rendered = prompt.split(marker, maxsplit=1)[1]
    schema = json.loads(rendered)

    assert schema == _provider_response_schema(SourceDossierProposal)
    assert set(schema["properties"]) == {"executive_synthesis", "items"}


def test_migrations_directory_resolves_from_installed_source_tree() -> None:
    assert MIGRATIONS_DIR == Path(__file__).resolve().parents[1] / "migrations"

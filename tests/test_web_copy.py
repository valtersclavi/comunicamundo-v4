from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_zero_source_flow_has_no_manual_source_search_copy() -> None:
    app = (ROOT / "src/comunicamundo_v4/web/assets/app.js").read_text()

    assert "Aprovar pré-projeto" in app
    assert "buscar fontes automaticamente" in app
    assert "Aprovando pré-projeto…" in app
    assert "Buscar fontes" not in app

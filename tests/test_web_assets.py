from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_web_ui_surfaces_operational_summary_and_deliverables() -> None:
    html = (ROOT / "src/comunicamundo_v4/web/index.html").read_text()
    app = (ROOT / "src/comunicamundo_v4/web/assets/app.js").read_text()

    assert "summary-panel" in html
    assert "Custo, tempo e entregáveis" in html
    assert "deliverables" in html
    assert "renderSummary" in app
    assert "Fontes encontradas" in app
    assert "Fontes adquiridas" in app
    assert "Projeto Editorial final" in app

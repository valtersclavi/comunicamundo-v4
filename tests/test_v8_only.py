"""The installed product must expose only V8 orchestration."""
from pathlib import Path
from fastapi.testclient import TestClient
from comunicamundo_v8.hefesto.api import create_app
from comunicamundo_v8.hefesto.worker import parser
from test_api import Auth


def test_only_v8_is_served_and_authentication_is_preserved():
    client = TestClient(create_app(auth_store=Auth()), base_url="https://testserver")
    for path in ['/', '/v8']:
        assert 'Hefesto V8' in client.get(path).text
    for path in ['/v1/plans', '/assets/app.js']:
        assert client.get(path).status_code == 404
    assert client.get('/v8/projects').status_code == 401
    assert client.get('/v1/me').status_code == 401
    assert client.post('/v1/login', json={'username':'hefesto','password':'correct-password'}).status_code == 200
    assert client.get('/v1/me').status_code == 200
    assert client.post('/v1/logout').status_code == 204
    assert client.get('/v1/me').status_code == 401


def test_retired_editorial_modules_cannot_return():
    root = Path(__file__).parents[1] / 'src/comunicamundo_v8/hefesto'
    for name in ['service','prompts','atomic','gateway','source_research',
                 'contribution_research','source_editor','batching','source_leads']:
        assert not (root / f'{name}.py').exists()
    help_text = parser().format_help()
    assert 'v8-run' in help_text
    for command in ['resume-plan','produce-text','approve-project','source-search']:
        assert command not in help_text

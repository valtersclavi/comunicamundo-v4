from hashlib import sha256
import pytest


@pytest.fixture(autouse=True)
def offline_opportunity_discovery(monkeypatch):
    """Exercise suggestion verification with deterministic external search results."""
    from comunicamundo_v4.hefesto import source_leads
    from test_source_pipeline import candidate

    class Discovery:
        def discover_query(self, query, provider):
            return [candidate(candidate_id=sha256(b'round 0').hexdigest(),
                              title='round 0', url='https://example.org/0')]

    monkeypatch.setattr(source_leads, 'source_discovery', Discovery)

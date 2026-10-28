"""Every existing settings entry keeps its admin gate after the layout change."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from flask import url_for

ENDPOINTS = ['tools_page', 'users_page', 'topics_page', 'rss_feeds_page',
             'scheduled_fetches_page', 'ingestion_blocks_page', 'scrape_blocklist',
             'prompts_page', 'pipeline_schedule_page', 'llm_providers_page']

@pytest.fixture(scope='module')
def app():
    from aggregator import create_app
    with patch.dict('os.environ', SECRET_KEY='permission-fixture', DATABASE_URL='sqlite://'):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    return app

@pytest.mark.parametrize('endpoint', ENDPOINTS)
@pytest.mark.parametrize('role', ['reader', 'scraper'])
def test_settings_entry_rejects_non_admin(app, endpoint, role):
    user = SimpleNamespace(is_authenticated=True, has_role=lambda minimum: minimum == role)
    with app.test_request_context():
        path = url_for('admin.' + endpoint)
    with patch('aggregator.permissions.current_user', user):
        assert app.test_client().get(path).status_code == 403


def test_provider_secrets_never_render_in_cleartext(app):
    from aggregator.blueprints.admin.llm_settings import _provider_rows
    from aggregator import config_store
    with patch.dict('os.environ', GEMINI_API_KEY='fixture-private-provider-token'):
        text = repr(_provider_rows(config_store.field_states(rows={})))
    assert 'fixture-private-provider-token' not in text
    assert 'oken' in text

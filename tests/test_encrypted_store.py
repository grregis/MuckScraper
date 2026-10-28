"""Database round-trip, restore, clearing, and write-access checks for saved secrets."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from cryptography.fernet import Fernet


@pytest.fixture
def app():
    from aggregator import create_app, db
    from aggregator.models import ConfigSetting
    with patch.dict('os.environ', SECRET_KEY='secret-store-fixture', CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode(), DATABASE_URL='sqlite://'):
        app = create_app()
        app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
        with app.app_context():
            ConfigSetting.__table__.create(db.engine)
            yield app
            db.session.remove()


def test_database_contains_ciphertext_and_restore_needs_original_key(app):
    from aggregator import db, config_store
    from aggregator.models import ConfigSetting
    config_store.save('NEWS_API_KEY', 'private-newsapi-token', 'admin')
    db.session.commit()
    row = db.session.get(ConfigSetting, 'NEWS_API_KEY')
    token = row.secret_value
    assert row.value is None and 'private-newsapi-token' not in token
    assert row.hint == 'oken'
    assert config_store._read_overrides()['NEWS_API_KEY'] == 'private-newsapi-token'
    # Restoring the ciphertext with the same key works; another key flags it.
    with patch.dict('os.environ', CONFIG_ENCRYPTION_KEY=Fernet.generate_key().decode()):
        assert config_store.field_states()['NEWS_API_KEY']['unreadable']
        assert 'NEWS_API_KEY' not in config_store._read_overrides()
    assert config_store._read_overrides()['NEWS_API_KEY'] == 'private-newsapi-token'
    assert 'private-newsapi-token' not in repr(config_store.field_states())


def test_clear_removes_override_and_environment_remains_available(app):
    from aggregator import db, config_store
    from news_fetcher import saved_settings
    config_store.save('NEWS_API_KEY', 'saved-token', 'admin')
    db.session.commit()
    config_store.save('NEWS_API_KEY', '', 'admin')
    db.session.commit()
    assert 'NEWS_API_KEY' not in config_store._read_overrides()
    with patch.dict('os.environ', NEWS_API_KEY='environment-token'):
        config_store.reload_now()
        assert saved_settings.get('NEWS_API_KEY') == 'environment-token'


@pytest.mark.parametrize('path', ['/admin/settings/source-keys/newsapi', '/admin/settings/llm/routing', '/admin/settings/llm/provider/ollama'])
def test_non_admin_cannot_write_settings(app, path):
    user = SimpleNamespace(is_authenticated=True, has_role=lambda minimum: False)
    with patch('aggregator.permissions.current_user', user):
        assert app.test_client().post(path).status_code == 403


def test_admin_settings_pages_render_without_exposing_saved_keys(app):
    from aggregator import db, config_store
    from aggregator.models import User, AppSetting, Topic, RssFeed, PromptTemplate, ScheduledFetch
    for model in (User, AppSetting, Topic, RssFeed, PromptTemplate, ScheduledFetch):
        model.__table__.create(db.engine)
    user = User(username='sample-admin', email='admin@example.com', role='admin')
    user.set_password('password123')
    db.session.add(user)
    config_store.save('GEMINI_API_KEY', 'private-browser-fixture-key', 'sample-admin')
    db.session.commit()
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = str(user.id)
        session['_fresh'] = True
    for path in ('/admin/settings/llm', '/admin/settings/source-keys', '/admin/topics'):
        response = client.get(path)
        assert response.status_code == 200
        assert 'private-browser-fixture-key' not in response.text
        assert response.text.count('<title>') == 1

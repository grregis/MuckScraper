"""Saved filters are per-account, validated, and never redirect off-site."""
from unittest.mock import patch

import pytest


@pytest.fixture
def app():
    from aggregator import create_app, db
    from aggregator.models import AppSetting, User
    with patch.dict('os.environ', DATABASE_URL='sqlite://', SECRET_KEY='fixture-key'):
        app = create_app()
    app.config.update(TESTING=True, WTF_CSRF_ENABLED=False)
    with app.app_context():
        User.__table__.create(db.engine)
        AppSetting.__table__.create(db.engine)
        for name in ('first', 'second'):
            user = User(username=name, email=name+'@example.com', role='reader')
            user.set_password('password123')
            db.session.add(user)
        db.session.commit()
    return app


def test_saved_filters_are_validated_and_belong_to_the_current_user(app):
    from aggregator import db, feed_prefs
    from aggregator.models import User
    client = app.test_client()
    with client.session_transaction() as session:
        session['_user_id'] = '1'
        session['_fresh'] = True
    response = client.post('/auth/feed-defaults', data={'range':'30d', 'hide_single':'true',
        'scrape_status':'not-valid', 'next':'https://elsewhere.example/'})
    assert response.status_code == 302
    assert response.location == '/admin/articles'
    with app.app_context():
        first = feed_prefs.load(db.session.get(User, 1))
        second = feed_prefs.load(db.session.get(User, 2))
        assert first['range'] == '30d' and first['hide_single'] is True
        assert first['scrape_status'] == 'all'
        assert second == feed_prefs.DEFAULTS


def test_signed_out_cannot_save_filters(app):
    response = app.test_client().post('/auth/feed-defaults', data={'range':'30d'})
    assert response.status_code == 302
    assert '/auth/login' in response.location

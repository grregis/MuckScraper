"""Provider logout retention must not enlarge cookies or become authentication."""
from datetime import datetime, timedelta
from unittest.mock import Mock
from cryptography.fernet import Fernet

import pytest
from urllib.parse import parse_qs, urlsplit

from test_oidc import app, flow  # Reuse signed-token and isolated account fixtures.
from aggregator import db
from aggregator.models import OIDCLogoutToken
from aggregator.oidc import logout_request
from aggregator.oidc_logout import MAX_TOKEN_BYTES, consume, discard, retain


def enable(app, monkeypatch):
    config = app.config['OIDC']
    config['POST_LOGOUT_REDIRECT_URI'] = 'https://reader.example/auth/oidc/logout/callback'
    remote = app.extensions['muckscraper_oidc']
    metadata = dict(remote.load_server_metadata(), end_session_endpoint='https://issuer.example/logout')
    monkeypatch.setattr(remote, 'load_server_metadata', Mock(return_value=metadata))
    return config


def logout(client):
    import re
    html = client.get('/auth/profile', base_url='https://reader.example').get_data(as_text=True)
    csrf = re.search(r'name="csrf_token" value="([^"]+)"', html).group(1)
    return client.post('/auth/logout', data={'csrf_token': csrf},
                       headers={'Referer': 'https://reader.example/auth/profile'}, base_url='https://reader.example')


def test_token_is_encrypted_server_side_and_single_use(app, flow, monkeypatch):
    client, start, finish = flow
    config = enable(app, monkeypatch)
    finish(start())
    with client.session_transaction() as data:
        handle, identity = data['oidc_logout_handle'], data['oidc_identity']
        assert len(handle) == 43
        assert 'id_token' not in data
        assert len(app.session_interface.get_signing_serializer(app).dumps(dict(data))) < 1800
    with app.app_context():
        row = OIDCLogoutToken.query.one()
        assert row.handle_hash != handle
        encrypted = row.encrypted_token
        assert encrypted.startswith('gAAAA')
        assert consume(handle, 'another-identity', config) is None
        assert consume(handle, identity, {**config, 'CLIENT_ID': 'another-client'}) is None
        assert consume(handle, identity, {**config, 'ISSUER_URL': 'https://other.example'}) is None
        token = consume(handle, identity, config)
        assert token.count('.') == 2
        assert token not in encrypted
        assert consume(handle, identity, config) is None
        assert OIDCLogoutToken.query.count() == 0


def test_provider_redirect_has_hint_and_no_cache_or_referrer(app, flow, monkeypatch):
    client, start, finish = flow
    enable(app, monkeypatch)
    finish(start())
    response = logout(client)
    assert response.status_code == 302
    assert response.headers['Cache-Control'] == 'no-store'
    assert response.headers['Referrer-Policy'] == 'no-referrer'
    token = parse_qs(urlsplit(response.location).query)['id_token_hint'][0]
    assert token.count('.') == 2
    with app.app_context():
        assert OIDCLogoutToken.query.count() == 0
    with client.session_transaction() as data:
        assert '_user_id' not in data and data['oidc_auto_login_paused']
        assert token not in str(dict(data))


@pytest.mark.parametrize('failure', ['expired', 'tampered', 'rotated-key', 'missing', 'old-session', 'wrong-client'])
def test_unusable_hint_still_logs_out_locally(app, flow, monkeypatch, failure):
    client, start, finish = flow
    enable(app, monkeypatch)
    finish(start())
    if failure == 'old-session':
        with client.session_transaction() as data:
            data.pop('oidc_logout_handle')
    elif failure == 'wrong-client':
        app.config['OIDC']['CLIENT_ID'] = 'changed-client'
    else:
        with app.app_context():
            row = OIDCLogoutToken.query.one()
            if failure == 'expired':
                row.expires_at = datetime.utcnow() - timedelta(seconds=1)
            elif failure == 'tampered':
                row.encrypted_token = 'invalid'
            elif failure == 'missing':
                db.session.delete(row)
            elif failure == 'rotated-key':
                # Keep the cookie key stable while changing only the retention cipher.
                import aggregator.oidc_logout as storage
                monkeypatch.setattr(storage, '_cipher', lambda: Fernet(Fernet.generate_key()))
            db.session.commit()
    assert logout(client).status_code == 302
    with client.session_transaction() as data:
        assert '_user_id' not in data and data['oidc_auto_login_paused']


def test_large_tokens_do_not_reach_cookie_and_bound_is_enforced(app, flow, monkeypatch):
    client, start, finish = flow
    config = enable(app, monkeypatch)
    finish(start(), name='x' * 12000)
    with client.session_transaction() as data:
        assert len(app.session_interface.get_signing_serializer(app).dumps(dict(data))) < 1800
        handle, identity = data['oidc_logout_handle'], data['oidc_identity']
    with app.app_context():
        assert len(consume(handle, identity, config)) > 12000
        for token in [None, {}, '', '\u2603', 'x' * (MAX_TOKEN_BYTES + 1)]:
            assert retain(token, identity, config, __import__('time').time() + 60) is None
        assert OIDCLogoutToken.query.count() == 0


def test_superseding_sign_in_and_session_expiry_delete_record(app, flow, monkeypatch):
    client, start, finish = flow
    enable(app, monkeypatch)
    finish(start())
    with client.session_transaction() as data:
        first = data['oidc_logout_handle']
    finish(start())
    with client.session_transaction() as data:
        assert data['oidc_logout_handle'] != first
        data['oidc_expires_at'] = 0
    with app.app_context():
        assert OIDCLogoutToken.query.count() == 1
    client.get('/auth/profile', base_url='https://reader.example')
    with app.app_context():
        assert OIDCLogoutToken.query.count() == 0


def test_retention_database_failure_does_not_break_login(app, flow, monkeypatch, caplog):
    client, start, finish = flow
    enable(app, monkeypatch)
    with app.app_context():
        OIDCLogoutToken.__table__.drop(db.engine)
    try:
        assert finish(start())[0].status_code == 302
        assert logout(client).status_code == 302
        assert 'SECRET_SENTINEL' not in caplog.text and 'INSERT INTO' not in caplog.text
    finally:
        with app.app_context():
            OIDCLogoutToken.__table__.create(db.engine)


def test_disabled_logout_needs_no_retention_table(app, flow):
    client, start, finish = flow
    with app.app_context():
        OIDCLogoutToken.__table__.drop(db.engine)
    try:
        assert finish(start())[0].status_code == 302
        assert logout(client).status_code == 302
    finally:
        with app.app_context():
            OIDCLogoutToken.__table__.create(db.engine)


def test_reserved_endpoint_hint_cannot_override_retained_token():
    config = {'CLIENT_ID': 'fixture', 'POST_LOGOUT_REDIRECT_URI': 'https://reader.example/callback'}
    endpoint, state = logout_request(config, 'https://issuer.example/logout?id_token_hint=attacker&state=old', 'verified')
    assert 'attacker' not in endpoint and 'state=old' not in endpoint and parse_qs(urlsplit(endpoint).query)['id_token_hint'] == ['verified']


def test_logout_storage_failure_still_clears_local_session(app, flow, monkeypatch, caplog):
    client, start, finish = flow
    enable(app, monkeypatch)
    finish(start())
    with app.app_context():
        OIDCLogoutToken.__table__.drop(db.engine)
    try:
        assert logout(client).status_code == 302
        with client.session_transaction() as data:
            assert '_user_id' not in data and data['oidc_auto_login_paused']
        assert 'SELECT ' not in caplog.text and 'SECRET_SENTINEL' not in caplog.text
    finally:
        with app.app_context():
            OIDCLogoutToken.__table__.create(db.engine)


def test_additive_migration_preserves_existing_accounts(app):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from aggregator.models import User, OIDCIdentity

    path = Path(__file__).resolve().parents[1] / 'migrations'
    config = Config(str(path / 'alembic.ini'))
    config.set_main_option('script_location', str(path))
    # One head, with the logout-token migration on its chain (later
    # migrations, such as config_settings, follow it).
    script = ScriptDirectory.from_config(config)
    assert len(script.get_heads()) == 1
    assert 'b8d2e4f6a901' in {rev.revision for rev in script.walk_revisions()}
    spec = importlib.util.spec_from_file_location('logout_migration', path / 'versions/b8d2e4f6a901_add_oidc_logout_tokens.py')
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    with app.app_context():
        user = User(username='existing', email='existing@example.invalid', password_hash='unchanged-hash', is_admin=True)
        db.session.add(user)
        db.session.flush()
        db.session.add(OIDCIdentity(identity_key='existing-binding', issuer='https://issuer.example', subject='existing-subject', user_id=user.id))
        db.session.commit()
        OIDCLogoutToken.__table__.drop(db.engine)
        for operation in [migration.upgrade, migration.downgrade, migration.upgrade]:
            with db.engine.begin() as connection:
                with Operations.context(MigrationContext.configure(connection)):
                    operation()
            db.session.expire_all()
            existing = User.query.one()
            assert (existing.username, existing.password_hash, existing.is_admin) == ('existing', 'unchanged-hash', True)
            assert OIDCIdentity.query.one().user_id == existing.id

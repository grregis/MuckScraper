"""Bounded, encrypted ID-token retention for optional provider logout only."""
import base64
import hashlib
import hmac
import secrets
from datetime import datetime

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from sqlalchemy.exc import SQLAlchemyError

from aggregator import db
from aggregator.models import OIDCIdentity, OIDCLogoutToken

MAX_TOKEN_BYTES = 65536


def _cipher():
    secret = current_app.secret_key
    if isinstance(secret, str):
        secret = secret.encode()
    key = hmac.new(secret, b"muckscraper/oidc-logout/v1", hashlib.sha256).digest()
    return Fernet(base64.urlsafe_b64encode(key))


def _handle_hash(handle):
    if not isinstance(handle, str) or len(handle) != 43 or not handle.isascii():
        return None
    return hashlib.sha256(handle.encode()).hexdigest()


def discard(handle):
    """Best-effort cleanup must never prevent local logout or session expiry."""
    try:
        now = datetime.utcnow()
        query = OIDCLogoutToken.query.filter(OIDCLogoutToken.expires_at <= now)
        digest = _handle_hash(handle)
        if digest:
            query = OIDCLogoutToken.query.filter(db.or_(
                OIDCLogoutToken.expires_at <= now, OIDCLogoutToken.handle_hash == digest))
        query.delete(synchronize_session=False)
        db.session.commit()
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning("OIDC logout token cleanup unavailable")


def retain(token, identity_key, config, expires_at):
    """Return an opaque cookie handle. Never store access or refresh tokens."""
    if not isinstance(token, str) or not token.isascii() or not 0 < len(token) <= MAX_TOKEN_BYTES:
        return None
    try:
        OIDCLogoutToken.query.filter(OIDCLogoutToken.expires_at <= datetime.utcnow()).delete(synchronize_session=False)
        handle = secrets.token_urlsafe(32)
        db.session.add(OIDCLogoutToken(
            handle_hash=_handle_hash(handle), identity_key=identity_key,
            client_id=config["CLIENT_ID"], encrypted_token=_cipher().encrypt(token.encode()).decode(),
            expires_at=datetime.utcfromtimestamp(expires_at)))
        db.session.commit()
        return handle
    except SQLAlchemyError:
        db.session.rollback()
        current_app.logger.warning("OIDC logout token retention unavailable")
        return None


def consume(handle, identity_key, config):
    """Atomically remove the token before it is sent to its issuing provider."""
    digest = _handle_hash(handle)
    if not digest:
        return None
    try:
        row = OIDCLogoutToken.query.filter_by(handle_hash=digest, identity_key=identity_key,
                                             client_id=config.get("CLIENT_ID")).first()
        binding = OIDCIdentity.query.filter_by(identity_key=identity_key, issuer=config.get("ISSUER_URL")).first()
        if not row or not binding:
            return None
        encrypted, expires = row.encrypted_token, row.expires_at
        # Conditional DELETE admits only one of two concurrent logout requests.
        deleted = OIDCLogoutToken.query.filter_by(handle_hash=digest, identity_key=identity_key,
                                                  client_id=config.get("CLIENT_ID")).delete(synchronize_session=False)
        db.session.commit()
        if deleted != 1 or expires <= datetime.utcnow():
            return None
        return _cipher().decrypt(encrypted.encode()).decode()
    except (SQLAlchemyError, InvalidToken, UnicodeError, ValueError):
        db.session.rollback()
        current_app.logger.warning("OIDC logout token unavailable")
        return None

"""Encryption for secrets saved in the database (API keys entered in
Settings).

Fernet (AES-128-CBC with an HMAC-SHA256 tag) from `cryptography`. The key
is CONFIG_ENCRYPTION_KEY when set (a Fernet key: 32 url-safe base64 bytes,
`python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`),
otherwise one derived from SECRET_KEY with HKDF, so an install works
without new configuration. Either way the key lives only in the
environment: a copy of the database alone does not reveal the secrets.

Changing the key (or SECRET_KEY, when it is the source) makes the saved
secrets unreadable; decrypt() then returns None and Settings asks for the
key to be entered again.
"""
import base64
import logging
import os

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

_HKDF_INFO = b"muckscraper-config-settings-v1"


def _fernet():
    explicit = os.environ.get("CONFIG_ENCRYPTION_KEY", "").strip()
    if explicit:
        return Fernet(explicit.encode())
    secret = os.environ.get("SECRET_KEY", "")
    if not secret:
        raise RuntimeError("SECRET_KEY or CONFIG_ENCRYPTION_KEY must be set to store secrets")
    derived = HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=_HKDF_INFO).derive(secret.encode())
    return Fernet(base64.urlsafe_b64encode(derived))


def key_source():
    """Where the encryption key comes from, for the settings page."""
    return "CONFIG_ENCRYPTION_KEY" if os.environ.get("CONFIG_ENCRYPTION_KEY", "").strip() else "SECRET_KEY"


def encrypt(plaintext):
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token):
    """The plaintext, or None when the token can't be read with the current key."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except (InvalidToken, ValueError) as e:
        logger.warning("[secret_box] Could not decrypt a saved secret (%s); was the key changed?", type(e).__name__)
        return None

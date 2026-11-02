"""Encryption for secrets saved in the database (API keys entered in
Settings).

Fernet (AES-128-CBC with an HMAC-SHA256 tag) from `cryptography`. The key is
CONFIG_ENCRYPTION_KEY: a Fernet key (32 url-safe base64 bytes; install.sh
generates one). Nothing else supplies it, so rotating SECRET_KEY never makes
saved secrets unreadable, and a copy of the database alone does not reveal
them. The key lives only in the environment.

Without the key, saving a secret is refused and a saved one can't be read.
Changing the key makes the saved secrets unreadable: decrypt() then returns
None, and Settings asks for the key to be entered again.
"""
import logging
import os

from cryptography.fernet import Fernet, InvalidToken

logger = logging.getLogger(__name__)


class KeyNotConfigured(RuntimeError):
    """Saving or reading a secret needs CONFIG_ENCRYPTION_KEY, which is unset."""


def _key():
    return os.environ.get("CONFIG_ENCRYPTION_KEY", "").strip()


def key_configured():
    return bool(_key())


def _fernet():
    key = _key()
    if not key:
        raise KeyNotConfigured("CONFIG_ENCRYPTION_KEY must be set to save secrets")
    return Fernet(key.encode())


def key_source():
    """"CONFIG_ENCRYPTION_KEY" when the key is set, else None (for the settings page)."""
    return "CONFIG_ENCRYPTION_KEY" if key_configured() else None


def encrypt(plaintext):
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(token):
    """The plaintext, or None when the token can't be read with the current key."""
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except KeyNotConfigured:
        logger.warning("[secret_box] A saved secret can't be read: CONFIG_ENCRYPTION_KEY is not set")
        return None
    except (InvalidToken, ValueError) as e:
        logger.warning("[secret_box] Could not decrypt a saved secret (%s); was the key changed?", type(e).__name__)
        return None

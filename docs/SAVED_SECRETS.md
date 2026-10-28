# Saved API keys: proposal and recovery notes

This is the encrypted-settings part of issue #31. The settings layout is a separate change. Existing environment-only installs keep working; saving an override is optional. This branch supports the upstream NewsAPI and GNews integrations and the LLM providers. It doesn't add the fork's NYT ingestion work.

## Where the key lives

API keys entered by an admin are Fernet-encrypted before being written to `config_settings.secret_value`. The plain `value` column is only for non-secret model names, hosts and routing. Pages get a last-four-character hint, never a saved plaintext key. Forms are CSRF-protected and saving requires Admin.

`CONFIG_ENCRYPTION_KEY` is a Fernet key supplied through the environment. It isn't written into the database. Use the same value for the app, scheduler and any process reading saved settings. The compose file forwards it alongside `SECRET_KEY`.

Generate a dedicated key with:

```sh
python3 -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

With `CONFIG_ENCRYPTION_KEY` blank, the implementation derives a key from `SECRET_KEY` using HKDF-SHA256 and a fixed application-specific context. That preserves the fork's existing behavior. A dedicated key is preferable: session-key rotation can then happen independently.

The app must decrypt keys to call providers. This protects a database-only backup, not a compromised application host or an attacker holding both the database and its encryption key.

## Backup and restore

Back up the database and the encryption key separately. Keep the key in a protected secret store or equivalent access-controlled backup, rather than in the database dump. If using the fallback, preserve the original `SECRET_KEY` instead.

Restore the original key into all processes before reading the restored database. With the same key, the encrypted rows work unchanged. With a missing or different key, Settings flags each saved key as unreadable; the ciphertext remains in the database. Restore the original key or enter the provider key again. There is no recovery from ciphertext alone.

The current implementation ignores an unreadable override at runtime, so an environment value can take its place. That behavior needs agreement during review; we could instead reject calls that depend on an unreadable saved key. A temporary database-read failure also needs review because the current cache can fall back to environment values.

## Rotation and clearing

Setting a new encryption key does not re-encrypt existing rows. Keep the old key available, recover or re-enter the saved provider keys, then verify every process uses the replacement. There is no automatic key-rotation command in this proposal. Never overwrite the only copy of the old key before recovery is complete.

Clearing a saved field removes the override and restores environment/default behavior. Changing or clearing an override refreshes the current process; other processes reread saved settings within about 15 seconds.

## Migration and rollback

The migration adds `config_settings` and skips creation if `create_all()` already made that table. It doesn't import environment keys into the database or rewrite article tables. Its downgrade deletes the saved-settings table, including encrypted keys. Preserve a database backup and the encryption key before downgrading.

The migration is based on the current upstream migration head. It must be rebased after #28 (and the search migration in #32 if that lands first) so the series has one agreed migration chain.

## Checks and open review

Tests cover encryption, key mismatch, write-only field state, a database round trip, environment fallback after clearing, and non-admin access. They use synthetic credentials and SQLite for the settings-table checks. They do not establish PostgreSQL upgrade/downgrade behavior or independent security review.

This PR stays draft pending that review and the maintainer's decision about dedicated-key requirements, unreadable-key behavior, backup/restore and rotation. The Anthropic dispatch code travels with this slice because the fork's saved provider choices include it; no provider call is made by these tests.

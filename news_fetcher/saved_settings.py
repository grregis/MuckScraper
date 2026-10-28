"""Settings saved from the admin UI, overriding environment variables.

The app installs a source (aggregator/config_store.py) that reads the saved
values from the database, decrypting keys, at most every RELOAD_SECONDS.
Code here in news_fetcher reads a setting with get(), which falls back to
the environment when nothing is saved, so a value saved in Settings takes
effect in the scheduler without a restart. With no source installed (tests,
scripts that never create the app) get() is just os.environ.
"""
import logging
import os

logger = logging.getLogger(__name__)

# Returns {name: plaintext} when the saved settings changed since its last
# call, else None.
_source = None
_overrides = {}
_version = 0


def set_source(source):
    global _source, _overrides, _version
    _source = source
    _overrides = {}
    _version += 1


def refresh():
    global _overrides, _version
    if _source is None:
        return
    try:
        overrides = _source()
    except Exception as e:  # a settings read must never break a pipeline call
        logger.warning(f"[saved_settings] Could not read saved settings: {e}")
        return
    if overrides is not None:
        _overrides = dict(overrides)
        _version += 1


def snapshot():
    """(version, {name: value}); the version changes whenever the values do."""
    refresh()
    return _version, _overrides


def get(name, default=""):
    """The saved value of `name`, else the environment variable, else `default`."""
    refresh()
    value = _overrides.get(name)
    if value is None or not str(value).strip():
        value = os.environ.get(name, default)
    return str(value).strip()

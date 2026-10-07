"""Process-wide plumbing: the read-only Flask app, the proxy client, redaction."""
import json
import os
import threading

import requests

from mcp_server import verdicts

# Every connection this process opens starts its transactions read-only, so a
# bug in a tool cannot write: Postgres refuses with "cannot execute ... in a
# read-only transaction". Set here, not only in compose, so it holds however
# the server is launched.
READ_ONLY_OPTIONS = "-c default_transaction_read_only=on"

_app = None
_app_lock = threading.Lock()


def repo_root():
    return os.environ.get("MCP_REPO_ROOT", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def get_app():
    global _app
    with _app_lock:
        if _app is None:
            from aggregator import create_app
            app = create_app()
            options = app.config.setdefault("SQLALCHEMY_ENGINE_OPTIONS", {})
            connect_args = options.setdefault("connect_args", {})
            connect_args["options"] = (connect_args.get("options", "") + " " + READ_ONLY_OPTIONS).strip()
            options.setdefault("pool_pre_ping", True)
            _app = app
    return _app


def run_read_only(fn, *args, **kwargs):
    """Run fn inside an app context; always roll back and release the session."""
    from aggregator import db
    app = get_app()
    with app.app_context():
        try:
            return fn(*args, **kwargs)
        finally:
            db.session.rollback()
            db.session.remove()


def proxy_get(path, **params):
    base = os.environ.get("DOCKER_RESTART_PROXY_URL", "http://docker-restart-proxy:5001").rstrip("/")
    try:
        r = requests.get(base + path, params=params, timeout=15)
        r.raise_for_status()
        return r.json()
    except Exception as e:
        return {"error": f"docker-restart-proxy unavailable: {str(e)[:200]}"}


_SECRETS = None


def scrub(result):
    """JSON round-trip with secret values and inline credentials masked."""
    global _SECRETS
    if _SECRETS is None:
        _SECRETS = verdicts.secret_values()
    return json.loads(verdicts.redact(json.dumps(result, default=str), _SECRETS))

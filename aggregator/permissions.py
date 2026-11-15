"""Who may see and do what in the aggregator app.

Roles (aggregator/models.py): reader < scraper < admin, each including the
ones below it. Signed-out visitors see Headlines, story pages and article
summaries only when PUBLIC_READ_ACCESS is on; otherwise every page needs an
account. Routes declare their minimum with @role_required(...) or
@public_or_login, and templates ask can('scraper') before showing a button.
"""
import os
from functools import wraps

from flask import abort, current_app
from flask_login import current_user

from aggregator.models import ROLE_ADMIN, ROLE_READER, ROLE_SCRAPER, ROLES  # noqa: F401  (re-exported)


def public_read_access_from_env():
    return os.environ.get("PUBLIC_READ_ACCESS", "false").strip().lower() in ("1", "true", "yes", "on")


def public_read_enabled():
    return bool(current_app.config.get("PUBLIC_READ_ACCESS"))


def can(minimum):
    """True when the current user is signed in with at least `minimum`."""
    return current_user.is_authenticated and current_user.has_role(minimum)


def role_required(minimum):
    """Signed-out users go to the login page; signed-in users below `minimum` get 403."""
    def decorator(view):
        @wraps(view)
        def wrapped(*args, **kwargs):
            if not current_user.is_authenticated:
                return current_app.login_manager.unauthorized()
            if not current_user.has_role(minimum):
                abort(403)
            return view(*args, **kwargs)
        return wrapped
    return decorator


def public_or_login(view):
    """Open to everyone when PUBLIC_READ_ACCESS is on, otherwise needs an account."""
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not current_user.is_authenticated and not public_read_enabled():
            return current_app.login_manager.unauthorized()
        return view(*args, **kwargs)
    return wrapped

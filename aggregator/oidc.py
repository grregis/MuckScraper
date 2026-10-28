"""Optional OpenID Connect login using the existing account and permission model.

Only trusted server configuration selects the issuer and role policy. Identity
is an exact issuer/subject binding, never a matching email or forwarded header.
"""
import hashlib
import json
import secrets
import time
from datetime import datetime
from urllib.parse import urlsplit

import click
from flask import abort, current_app, redirect, request, session, url_for
from flask_login import current_user, login_user, logout_user

from aggregator import db
from aggregator.accounts import email_error, other_active_admins
from aggregator.models import OIDCIdentity, ROLE_ADMIN, ROLE_READER, ROLES, User


class OIDCRefused(ValueError):
    """A fixed refusal class; exception details must never reach logs or pages."""


def _switch(environ, name, default):
    value = environ.get(name, str(default)).lower().strip()
    if value not in {"true", "false", "1", "0"}:
        raise ValueError(f"{name} must be true or false")
    return value in {"true", "1"}


def _https_url(value, name):
    parsed = urlsplit(value)
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or
            parsed.password or parsed.query or parsed.fragment):
        raise ValueError(f"{name} must be an HTTPS URL without credentials, query or fragment")
    return value


def configuration(environ):
    if not _switch(environ, "OIDC_ENABLED", False):
        return None
    config = {}
    for name in ("ISSUER_URL", "CLIENT_ID", "CLIENT_SECRET", "REDIRECT_URI"):
        value = environ.get("OIDC_" + name, "")
        if not value.strip():
            raise ValueError(f"OIDC_{name} is required when OIDC is enabled")
        config[name] = value
    for name in ("ISSUER_URL", "REDIRECT_URI"):
        _https_url(config[name], "OIDC_" + name)
    config["SCOPES"] = environ.get("OIDC_SCOPES", "openid profile email groups")
    if "openid" not in config["SCOPES"].split():
        raise ValueError("OIDC_SCOPES must include openid")
    config["PROVIDER_NAME"] = environ.get("OIDC_PROVIDER_NAME", "OpenID Connect")
    config["GROUPS_CLAIM"] = environ.get("OIDC_GROUPS_CLAIM", "groups")
    if not config["GROUPS_CLAIM"] or not 1 <= len(config["PROVIDER_NAME"]) <= 100:
        raise ValueError("Invalid OIDC claim name or provider label")
    config["ALLOWED_GROUPS"] = json.loads(environ.get("OIDC_ALLOWED_GROUPS", "[]"))
    if (not isinstance(config["ALLOWED_GROUPS"], list) or
            any(not isinstance(g, str) or not g for g in config["ALLOWED_GROUPS"])):
        raise ValueError("OIDC_ALLOWED_GROUPS must be a JSON array of group names")
    config["ROLE_MAPPING"] = json.loads(environ.get("OIDC_ROLE_MAPPING") or "{}")
    if (not isinstance(config["ROLE_MAPPING"], dict) or any(
            not isinstance(g, str) or not g or role not in ROLES
            for g, role in config["ROLE_MAPPING"].items())):
        raise ValueError("OIDC_ROLE_MAPPING must map group names to supported roles")
    config["CREATE_USERS"] = _switch(environ, "OIDC_CREATE_USERS", False)
    config["ALLOW_PASSWORD_LOGIN"] = _switch(environ, "OIDC_ALLOW_PASSWORD_LOGIN", True)
    config["AUTO_LOGIN"] = _switch(environ, "OIDC_AUTO_LOGIN", False)
    config["SESSION_LIFETIME_SECONDS"] = int(environ.get("OIDC_SESSION_LIFETIME_SECONDS", "3600"))
    if not 60 <= config["SESSION_LIFETIME_SECONDS"] <= 86400:
        raise ValueError("OIDC_SESSION_LIFETIME_SECONDS must be between 60 and 86400")
    return config


def identity_key(issuer, subject):
    if not isinstance(subject, str) or not 1 <= len(subject) <= 255 or "\0" in subject:
        raise OIDCRefused()
    return hashlib.sha256((issuer + "\0" + subject).encode()).hexdigest()


def safe_next(value):
    # Accept local absolute paths only, not schemes, authority URLs or backslashes.
    if (not isinstance(value, str) or not value.startswith("/") or value.startswith("//")
            or "\\" in value or any(ord(c) < 32 for c in value)):
        return url_for("public.headlines_feed")
    parsed = urlsplit(value)
    return value if not parsed.scheme and not parsed.netloc else url_for("public.headlines_feed")


def display_name(claims):
    """Presentation only: never use these non-unique claims for account lookup."""
    for field in ("preferred_username", "name", "email"):
        value = claims.get(field)
        if isinstance(value, str) and value.strip() and len(value.strip()) <= 254:
            if not any(ord(char) < 32 or ord(char) == 127 for char in value):
                return value.strip()
    return "Account"


def resolve_user(claims, config):
    if claims.get("iss") != config["ISSUER_URL"] or claims.get("email_verified") is not True:
        raise OIDCRefused()
    key = identity_key(config["ISSUER_URL"], claims.get("sub"))
    groups = claims.get(config["GROUPS_CLAIM"], [])
    if not isinstance(groups, list) or any(not isinstance(g, str) for g in groups):
        raise OIDCRefused()
    if config["ALLOWED_GROUPS"] and not set(groups).intersection(config["ALLOWED_GROUPS"]):
        raise OIDCRefused()
    binding = OIDCIdentity.query.filter_by(identity_key=key).first()
    if binding:
        if (binding.issuer != config["ISSUER_URL"] or binding.subject != claims["sub"]
                or not binding.user.is_active):
            raise OIDCRefused()
        user = binding.user
    else:
        if not config["CREATE_USERS"]:
            raise OIDCRefused()
        email = claims.get("email")
        if not isinstance(email, str) or email_error(email):
            raise OIDCRefused()
        username = "oidc_" + key[:59]
        if User.query.filter_by(username=username).first():
            raise OIDCRefused()
        user = User(username=username, email=email, role=ROLE_READER, is_admin=False, is_active=True)
    if config["ROLE_MAPPING"]:
        role = max((config["ROLE_MAPPING"].get(g, ROLE_READER) for g in groups),
                   key=ROLES.index, default=ROLE_READER)
        if user.role == ROLE_ADMIN and role != ROLE_ADMIN:
            # Serialize simultaneous OIDC demotions on databases supporting row
            # locks; do not let two administrators each count the other first.
            User.query.filter(User.role == ROLE_ADMIN, User.is_active.is_(True)).order_by(
                User.id).with_for_update().all()
            if other_active_admins(user.id) == 0:
                raise OIDCRefused()
        user.set_role(role)
    user.last_login_at = datetime.utcnow()
    db.session.add(user)
    if not binding:
        db.session.add(OIDCIdentity(user=user, identity_key=key,
                                    issuer=config["ISSUER_URL"], subject=claims["sub"]))
    db.session.commit()
    return user, key


def init_app(app, environ):
    config = configuration(environ)
    app.config["OIDC"] = config
    @app.context_processor
    def account_presentation():
        if not current_user.is_authenticated:
            return {"account_display_name": "Account"}
        if session.get("auth_method") == "oidc":
            return {"account_display_name": session.get("oidc_display_name") or current_user.email or "Account"}
        return {"account_display_name": current_user.username}
    if config:
        from authlib.integrations.flask_client import OAuth

        oauth = OAuth(app)
        app.extensions["muckscraper_oidc"] = oauth.register(
            "muckscraper", client_id=config["CLIENT_ID"], client_secret=config["CLIENT_SECRET"],
            server_metadata_url=config["ISSUER_URL"].rstrip("/") + "/.well-known/openid-configuration",
            client_kwargs={"scope": config["SCOPES"], "code_challenge_method": "S256", "timeout": 15},
            claims_options={"iss": {"essential": True, "value": config["ISSUER_URL"]}},
        )
        app.config.update(SESSION_COOKIE_SECURE=True, SESSION_COOKIE_HTTPONLY=True,
                          SESSION_COOKIE_SAMESITE="Lax", REMEMBER_COOKIE_SECURE=True,
                          REMEMBER_COOKIE_HTTPONLY=True, REMEMBER_COOKIE_SAMESITE="Lax")

    @app.before_request
    def expire_oidc_session():
        if session.get("auth_method") == "oidc":
            key = session.get("oidc_identity")
            if (not config or session.get("oidc_expires_at", 0) <= time.time()
                    or not current_user.is_authenticated or not OIDCIdentity.query.filter_by(
                        identity_key=key, user_id=current_user.id, issuer=config["ISSUER_URL"]).first()):
                session.clear()
                logout_user()

    @app.after_request
    def private_auth_responses(response):
        if request.endpoint == "auth.login" or (request.endpoint and request.endpoint.startswith("auth.oidc_")):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Referrer-Policy"] = "same-origin" if request.endpoint == "auth.login" else "no-referrer"
        return response

    @app.cli.command("link-oidc-identity")
    @click.option("--username", required=True)
    @click.option("--subject", required=True)
    @click.option("--apply", "apply_binding", is_flag=True)
    def link_identity(username, subject, apply_binding):
        """Bind a verified provider subject to an existing account; dry-run by default."""
        if not config:
            raise click.ClickException("OIDC is not enabled")
        try:
            key = identity_key(config["ISSUER_URL"], subject)
        except OIDCRefused:
            raise click.ClickException("Invalid subject") from None
        user = User.query.filter_by(username=username, is_active=True).first()
        if not user:
            raise click.ClickException("Active account not found")
        binding = OIDCIdentity.query.filter_by(identity_key=key).first()
        if binding and (binding.user_id != user.id or binding.issuer != config["ISSUER_URL"]
                        or binding.subject != subject):
            raise click.ClickException("Identity already bound; replacement refused")
        if binding:
            click.echo("Binding already exists; no change.")
        elif apply_binding:
            db.session.add(OIDCIdentity(user=user, identity_key=key,
                                        issuer=config["ISSUER_URL"], subject=subject))
            db.session.commit()
            click.echo("Binding created.")
        else:
            click.echo("Dry-run: binding can be created; no change.")


def start():
    config = current_app.config.get("OIDC")
    if not config:
        abort(404)
    remote = current_app.extensions["muckscraper_oidc"]
    # Only one attempt is supported. Discard its old Authlib state too, or
    # abandoned redirects eventually overflow the signed session cookie.
    prefix = f"_state_{remote.name}_"
    for key in list(session):
        if key.startswith(prefix):
            remote.framework.clear_state_data(session, key[len(prefix):])
    state = secrets.token_urlsafe(32)
    # A failed or cancelled attempt must leave a usable manual login page.
    session["oidc_auto_login_paused"] = True
    session["oidc_pending"] = {"state": state, "next": safe_next(request.args.get("next"))}
    try:
        return remote.authorize_redirect(
            config["REDIRECT_URI"], state=state)
    except Exception as error:
        current_app.logger.warning("OIDC start refused (%s)", type(error).__name__)
        abort(503)


def callback():
    config = current_app.config.get("OIDC")
    if not config:
        abort(404)
    pending = session.get("oidc_pending", {})
    state = request.args.get("state", "")
    if not state or not state.isascii() or not secrets.compare_digest(state, pending.get("state", "")):
        abort(403)
    session.pop("oidc_pending", None)
    try:
        token = current_app.extensions["muckscraper_oidc"].authorize_access_token()
        if not token.get("id_token") or not token.get("userinfo"):
            raise OIDCRefused()
        user, key = resolve_user(token["userinfo"], config)
    except Exception as error:
        db.session.rollback()
        current_app.logger.warning("OIDC sign-in refused (%s)", type(error).__name__)
        abort(403)
    session.clear()
    logout_user()  # Also clears a stale local remember-me cookie.
    login_user(user, remember=False)
    session.update(auth_method="oidc", oidc_identity=key,
                   oidc_display_name=display_name(token["userinfo"]),
                   oidc_expires_at=time.time() + config["SESSION_LIFETIME_SECONDS"])
    return redirect(safe_next(pending.get("next")))

import secrets
from datetime import datetime

from urllib.parse import urlparse
from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from aggregator import db, feed_prefs
from aggregator.accounts import email_error, password_error
from aggregator.models import User

auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("public.headlines_feed"))

    oidc = current_app.config.get("OIDC")
    if (request.method == "GET" and oidc and oidc["AUTO_LOGIN"]
            and request.args.get("manual") != "1"
            and not session.get("oidc_auto_login_paused")):
        return redirect(url_for("auth.oidc_login", next=request.args.get("next", "")))
    allow_password = not oidc or oidc["ALLOW_PASSWORD_LOGIN"]
    if request.method == "POST" and not allow_password:
        abort(403)
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        remember = True if request.form.get("remember") else False

        user = User.query.filter_by(username=username).first()
        # A disabled account gets the same message as a wrong password, so the
        # form doesn't reveal which accounts exist.
        if user and user.is_active and user.check_password(password):
            login_user(user, remember=remember)
            user.last_login_at = datetime.utcnow()
            db.session.commit()
            from aggregator.oidc import safe_next
            next_page = request.args.get("next")
            return redirect(safe_next(next_page) if next_page else url_for("public.index"))
        else:
            flash("Invalid username or password")

    return render_template("login.html", oidc_enabled=bool(oidc),
                           oidc_provider_name=oidc["PROVIDER_NAME"] if oidc else "",
                           allow_password_login=allow_password)


@auth.route("/oidc/login", methods=["GET"])
def oidc_login():
    from aggregator.oidc import start
    return start()


@auth.route("/oidc/callback", methods=["GET"])
def oidc_callback():
    from aggregator.oidc import callback
    return callback()


@auth.route("/logout", methods=["GET", "POST"])
def logout():
    oidc_session = session.get("auth_method") == "oidc"
    if oidc_session and request.method != "POST":
        abort(405)
    provider_logout = None
    if oidc_session:
        from aggregator.oidc import logout_request
        from aggregator.oidc_logout import consume, discard
        config = current_app.config.get("OIDC") or {}
        handle = session.get("oidc_logout_handle")
        token = consume(handle, session.get("oidc_identity"), config)
        if handle:
            discard(handle)
        provider_logout = logout_request(config, session.get("oidc_end_session_endpoint"), token)
    session.clear()
    logout_user()
    session["oidc_auto_login_paused"] = True
    if provider_logout:
        endpoint, state = provider_logout
        session["oidc_logout_state"] = state
        return redirect(endpoint)
    return redirect(url_for("public.index"))


@auth.route("/oidc/logout/callback", methods=["GET"])
def oidc_logout_callback():
    if not current_app.config.get("OIDC"):
        abort(404)
    expected = session.pop("oidc_logout_state", "")
    state = request.args.get("state", "")
    if not expected or not state or not state.isascii() or len(state) > 128 or not secrets.compare_digest(state, expected):
        abort(403)
    return redirect(url_for("auth.login", manual="1"))


@auth.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """The signed-in user's own account: change email or password.

    Both changes ask for the current password, so a session left open on a
    shared machine can't be used to take the account over.
    """
    oidc_managed = session.get("auth_method") == "oidc"
    # Feed defaults are preferences, so OIDC accounts keep them. Credentials
    # stay with the identity provider.
    if request.method == "POST" and oidc_managed and request.form.get("action") != "feed_defaults":
        abort(403)
    if request.method == "POST":
        action = request.form.get("action")
        if action == "feed_defaults":
            # Preferences, not credentials: no password needed.
            # An unticked checkbox sends nothing, so read presence.
            feed_prefs.save(current_user, {**request.form.to_dict(), "hide_single": "hide_single" in request.form})
            flash("Feed defaults saved.", "success")
            return redirect(url_for("auth.profile"))
        if not current_user.check_password(request.form.get("current_password", "")):
            flash("Your current password was not correct.", "error")
        elif action == "email":
            email = request.form.get("email", "").strip()
            error = email_error(email, exclude_id=current_user.id)
            if error:
                flash(error, "error")
            else:
                current_user.email = email
                db.session.commit()
                flash("Email address updated.", "success")
        elif action == "password":
            new_password = request.form.get("new_password", "")
            error = password_error(new_password, request.form.get("confirm_password", ""))
            if error:
                flash(error, "error")
            else:
                current_user.set_password(new_password)
                db.session.commit()
                flash("Password changed.", "success")
        return redirect(url_for("auth.profile"))

    return render_template(
        "profile.html",
        oidc_managed=oidc_managed,
        feed_defaults=feed_prefs.load(current_user),
        start_pages=feed_prefs.START_PAGES,
        ranges=feed_prefs.RANGES,
        scrape_statuses=feed_prefs.SCRAPE_STATUSES,
    )


def _local_next(value, fallback):
    """`value` if it is a path on this site, else `fallback`. Same rules as
    oidc.safe_next: browsers read a backslash as a slash, so "/\\host" would
    leave the site."""
    if (not value or not value.startswith("/") or value.startswith("//")
            or "\\" in value or any(ord(c) < 32 for c in value) or urlparse(value).netloc):
        return fallback
    return value


@auth.route("/feed-defaults", methods=["POST"])
@login_required
def save_feed_defaults():
    """"Save as my default" in the feed's Filters popover: store the filters
    the list is showing now and go back to it. Fields the form leaves out keep
    their saved values."""
    prefs = feed_prefs.load(current_user)
    for field in ("start_page", "range", "scrape_status", "hide_single"):
        if field in request.form:
            prefs[field] = request.form[field]
    feed_prefs.save(current_user, prefs)
    flash("Saved as your feed defaults.", "success")
    return redirect(_local_next(request.form.get("next"), url_for("admin.list_articles")))

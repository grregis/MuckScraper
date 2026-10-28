from datetime import datetime

from flask import Blueprint, abort, current_app, flash, redirect, render_template, request, session, url_for
from flask_login import current_user, login_required, login_user, logout_user

from aggregator import db
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
            return redirect(safe_next(request.args.get("next")))
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
    if session.get("auth_method") == "oidc" and request.method != "POST":
        abort(405)
    session.clear()
    logout_user()
    session["oidc_auto_login_paused"] = True
    return redirect(url_for("public.index"))


@auth.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """The signed-in user's own account: change email or password.

    Both changes ask for the current password, so a session left open on a
    shared machine can't be used to take the account over.
    """
    oidc_managed = session.get("auth_method") == "oidc"
    if request.method == "POST" and oidc_managed:
        abort(403)
    if request.method == "POST":
        action = request.form.get("action")
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

    return render_template("profile.html", oidc_managed=oidc_managed)

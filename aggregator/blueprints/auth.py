from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from aggregator import db
from aggregator.accounts import email_error, password_error
from aggregator.models import User

auth = Blueprint("auth", __name__)


@auth.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("public.headlines_feed"))

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
            next_page = request.args.get("next")
            if not next_page or urlparse(next_page).netloc != "":
                next_page = url_for("public.headlines_feed")
            return redirect(next_page)
        else:
            flash("Invalid username or password")

    return render_template("login.html")


@auth.route("/logout", methods=["GET", "POST"])
def logout():
    logout_user()
    return redirect(url_for("public.index"))


@auth.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    """The signed-in user's own account: change email or password.

    Both changes ask for the current password, so a session left open on a
    shared machine can't be used to take the account over.
    """
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

    return render_template("profile.html")

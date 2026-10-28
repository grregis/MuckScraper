from datetime import datetime
from urllib.parse import urlparse

from flask import Blueprint, flash, redirect, render_template, request, url_for
from flask_login import current_user, login_required, login_user, logout_user

from aggregator import db, feed_prefs
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
                # The index sends the reader to their chosen start page.
                next_page = url_for("public.index")
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
        feed_defaults=feed_prefs.load(current_user),
        start_pages=feed_prefs.START_PAGES,
        ranges=feed_prefs.RANGES,
        scrape_statuses=feed_prefs.SCRAPE_STATUSES,
    )


def _local_next(value, fallback):
    """`value` if it is a path on this site, else `fallback`."""
    if not value or not value.startswith("/") or value.startswith("//") or urlparse(value).netloc:
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

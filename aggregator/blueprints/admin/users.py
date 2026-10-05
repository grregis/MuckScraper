"""User administration (/admin/users): add accounts, change roles, disable,
reset passwords and delete. Admin only.

Two guards keep the app manageable: you can't change your own role, disable
or delete yourself (use your profile for your own email and password), and
an action that would leave no active admin is refused.
"""
from flask import flash, redirect, render_template, request, url_for
from flask_login import current_user

from aggregator import db
from aggregator.accounts import email_error, other_active_admins, password_error, username_error
from aggregator.models import ROLE_ADMIN, ROLES, User
from aggregator.permissions import role_required

from . import admin


def _back():
    return redirect(url_for("admin.users_page"))


@admin.route("/users")
@role_required(ROLE_ADMIN)
def users_page():
    users = User.query.order_by(User.username).all()
    return render_template("users.html", users=users, roles=ROLES)


@admin.route("/users/add", methods=["POST"])
@role_required(ROLE_ADMIN)
def add_user():
    username = request.form.get("username", "").strip()
    email = request.form.get("email", "").strip()
    role = request.form.get("role", "")
    password = request.form.get("password", "")
    error = (username_error(username) or email_error(email)
             or (None if role in ROLES else "Choose a role.") or password_error(password))
    if error:
        flash(error, "error")
        return _back()
    user = User(username=username, email=email)
    user.set_role(role)
    user.set_password(password)
    db.session.add(user)
    db.session.commit()
    flash(f"Added {username} as {user.role_label}.", "success")
    return _back()


@admin.route("/users/<int:user_id>/update", methods=["POST"])
@role_required(ROLE_ADMIN)
def update_user(user_id):
    user = User.query.get_or_404(user_id)
    email = request.form.get("email", "").strip()
    role = request.form.get("role", user.role)
    active = request.form.get("is_active") == "on"

    error = email_error(email, exclude_id=user.id) or (None if role in ROLES else "Choose a role.")
    if not error and user.id == current_user.id and (role != user.role or not active):
        error = "You can't change your own role or disable your own account."
    removes_admin = user.role == ROLE_ADMIN and user.is_active and (role != ROLE_ADMIN or not active)
    if not error and removes_admin and other_active_admins(user.id) == 0:
        error = "There must always be at least one active admin."
    if error:
        flash(error, "error")
        return _back()

    user.email = email
    user.set_role(role)
    user.is_active = active
    db.session.commit()
    flash(f"Saved {user.username}.", "success")
    return _back()


@admin.route("/users/<int:user_id>/password", methods=["POST"])
@role_required(ROLE_ADMIN)
def reset_user_password(user_id):
    user = User.query.get_or_404(user_id)
    password = request.form.get("password", "")
    error = password_error(password)
    if error:
        flash(error, "error")
        return _back()
    user.set_password(password)
    db.session.commit()
    flash(f"Set a new password for {user.username}. Let them know it, and suggest they change it on their profile.", "success")
    return _back()


@admin.route("/users/<int:user_id>/delete", methods=["POST"])
@role_required(ROLE_ADMIN)
def delete_user(user_id):
    user = User.query.get_or_404(user_id)
    if user.id == current_user.id:
        flash("You can't delete your own account.", "error")
        return _back()
    if user.role == ROLE_ADMIN and user.is_active and other_active_admins(user.id) == 0:
        flash("There must always be at least one active admin.", "error")
        return _back()
    db.session.delete(user)
    db.session.commit()
    flash(f"Deleted {user.username}.", "success")
    return _back()

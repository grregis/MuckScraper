"""Validation shared by the profile page and user administration.

Each check returns an error message for the person filling in the form, or
None when the value is acceptable.
"""
import re

from aggregator.models import ROLE_ADMIN, User

MIN_PASSWORD_LENGTH = 8
USERNAME_RE = re.compile(r"^[A-Za-z0-9_.-]{3,64}$")
# Deliberately loose: one @, something either side, a dot in the domain.
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def username_error(username, exclude_id=None):
    if not USERNAME_RE.match(username or ""):
        return "Usernames are 3 to 64 characters: letters, numbers, dots, dashes and underscores."
    query = User.query.filter(User.username == username)
    if exclude_id is not None:
        query = query.filter(User.id != exclude_id)
    if query.first():
        return f"The username {username} is already taken."
    return None


def email_error(email, exclude_id=None):
    if len(email or "") > 120 or not EMAIL_RE.match(email or ""):
        return "Enter a valid email address."
    query = User.query.filter(User.email == email)
    if exclude_id is not None:
        query = query.filter(User.id != exclude_id)
    if query.first():
        return "That email address is already used by another account."
    return None


def password_error(password, confirm=None):
    if len(password or "") < MIN_PASSWORD_LENGTH:
        return f"Passwords need at least {MIN_PASSWORD_LENGTH} characters."
    if confirm is not None and password != confirm:
        return "The two new passwords don't match."
    return None


def other_active_admins(user_id):
    """Active admins other than `user_id`: there must always be at least one."""
    return User.query.filter(User.role == ROLE_ADMIN, User.is_active.is_(True), User.id != user_id).count()

import os
import sys
import time

from sqlalchemy.exc import OperationalError
from flask_migrate import stamp

from aggregator import db
from aggregator.app import app, init_db
from aggregator.models import User


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set in .env")
    return value


def bootstrap_admin():
    username = required_env("ADMIN_USERNAME")
    email = required_env("ADMIN_EMAIL")
    password = required_env("ADMIN_PASSWORD")

    last_error = None
    for attempt in range(1, 31):
        try:
            init_db()
            break
        except OperationalError as exc:
            last_error = exc
            print(f"Database not ready yet, retrying ({attempt}/30)...")
            time.sleep(2)
    else:
        raise RuntimeError(f"Database did not become ready: {last_error}")

    with app.app_context():
        # Fresh installs created with db.create_all() need Alembic marked current
        # so later `flask db upgrade` runs only future migrations.
        stamp(revision="head")

        user = User.query.filter_by(username=username).first()
        email_owner = User.query.filter_by(email=email).first()

        if email_owner and email_owner.username != username:
            raise RuntimeError(
                f"ADMIN_EMAIL is already used by user '{email_owner.username}'. "
                "Choose a different ADMIN_EMAIL or update that user manually."
            )

        if user:
            user.email = email
            user.is_admin = True
            user.set_password(password)
            action = "updated"
        else:
            user = User(username=username, email=email, is_admin=True)
            user.set_password(password)
            db.session.add(user)
            action = "created"

        db.session.commit()
        print(f"Admin user '{username}' {action}.")

        # Seed the canonical DE topic set + RSS feeds (idempotent). Safe to run
        # on every bootstrap; existing rows are updated in place.
        try:
            import seed_topics
            result = seed_topics.run()
            print(f"Seed topics: {result}")
        except Exception as exc:
            print(f"Seed topics skipped: {exc}", file=sys.stderr)


if __name__ == "__main__":
    try:
        bootstrap_admin()
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        sys.exit(1)

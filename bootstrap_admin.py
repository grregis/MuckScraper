import os
import sys
import time

from sqlalchemy.exc import OperationalError
from flask_migrate import stamp

from aggregator import db
from aggregator.app import app, init_db
from aggregator.models import User
from aggregator.seed_defaults import seed_defaults


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

        # ...but stamping head also means the seeding migrations never run, and
        # can never run afterwards, so a fresh install would come up with all
        # seven config tables empty: nothing scheduled, nothing fetched, and no
        # prompts, hence no summaries/headlines/classification. Seed them here
        # instead. Insert-only and idempotent, so this is a no-op on an existing
        # install and cannot overwrite anything the operator has customized.
        counts = seed_defaults()
        inserted = sum(v for k, v in counts.items() if k != "topics_backfilled")
        print(
            f"Default config seeded: {inserted} row(s) inserted."
            if inserted else "Default config already present."
        )

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


if __name__ == "__main__":
    try:
        bootstrap_admin()
    except Exception as exc:
        print(f"Bootstrap failed: {exc}", file=sys.stderr)
        sys.exit(1)

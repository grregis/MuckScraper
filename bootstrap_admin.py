import os
import sys
import time

from sqlalchemy import inspect
from sqlalchemy.exc import OperationalError
from flask_migrate import stamp, upgrade

from aggregator import db
from aggregator.app import app, init_db
from aggregator.models import ROLE_ADMIN, User
from aggregator.seed_defaults import seed_defaults


def required_env(name):
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} must be set in .env")
    return value


def schema_state():
    """Classify the database BEFORE create_all() runs, since afterwards an
    empty database and an existing one look the same.

    "empty"     -- no application tables: a genuine fresh install.
    "managed"   -- alembic_version exists: an install Alembic already tracks.
    "unmanaged" -- application tables but no alembic_version: a pre-Alembic
                   install whose schema version cannot be known.
    """
    with app.app_context():
        tables = set(inspect(db.engine).get_table_names())
    if "alembic_version" in tables:
        return "managed"
    if "articles" in tables:
        return "unmanaged"
    return "empty"


def prepare_schema(state):
    """Bring the schema to head without ever marking a migration applied
    that did not run.

    Stamping head unconditionally (the old behaviour) was only right for an
    empty database: create_all() adds missing tables but never missing
    columns, so on an install behind on migrations the stamp recorded
    column-adding migrations as applied, and `flask db upgrade` could then
    never apply them -- a permanently missing column with no error.
    """
    if state == "unmanaged":
        raise RuntimeError(
            "This database has MuckScraper tables but no Alembic version, so "
            "which migrations it has is unknown and marking it current could "
            "skip real schema changes. Identify the revision that matches its "
            "schema, run `flask db stamp <revision>`, then `flask db upgrade`, "
            "and re-run this script."
        )

    if state == "managed":
        # Run pending migrations first: create_all() would otherwise create
        # any new tables itself and the migration adding them would then fail.
        with app.app_context():
            upgrade()
        init_db()
        return

    init_db()
    with app.app_context():
        # A fresh schema from create_all() is already at head, so marking it
        # so is true, and later `flask db upgrade` runs only future migrations.
        stamp(revision="head")


def bootstrap_admin():
    username = required_env("ADMIN_USERNAME")
    email = required_env("ADMIN_EMAIL")
    password = required_env("ADMIN_PASSWORD")

    last_error = None
    for attempt in range(1, 31):
        try:
            state = schema_state()
            break
        except OperationalError as exc:
            last_error = exc
            print(f"Database not ready yet, retrying ({attempt}/30)...")
            time.sleep(2)
    else:
        raise RuntimeError(f"Database did not become ready: {last_error}")

    print(f"Database state: {state}.")
    prepare_schema(state)

    with app.app_context():
        # On a fresh install stamping head means the seeding migrations never
        # run, and can never run afterwards, so it would come up with all
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
            user.set_role(ROLE_ADMIN)
            user.is_active = True
            user.set_password(password)
            action = "updated"
        else:
            user = User(username=username, email=email)
            user.set_role(ROLE_ADMIN)
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

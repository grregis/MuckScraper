from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
from flask_wtf import CSRFProtect
import os
import logging

logger = logging.getLogger(__name__)

db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = "auth.login"
csrf = CSRFProtect()

# SECRET_KEY values shipped in .env.sample, current and earlier.
PLACEHOLDER_SECRET_KEYS = {
    "replace_with_a_generated_secret_key",
    "generate_with_python3_-c_import_secrets_print_secrets.token_hex_32",
}


def create_app():
    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY environment variable must be set (see .env.sample)"
        )
    # The .env.sample placeholders are public, so a session signed with one
    # can be forged by anyone. install.sh replaces them; a hand-made .env may not.
    if secret_key in PLACEHOLDER_SECRET_KEYS:
        raise RuntimeError(
            "SECRET_KEY is still the .env.sample placeholder. Generate one with "
            "python3 -c \"import secrets; print(secrets.token_hex(32))\" "
            "and put it in .env"
        )
    app.config["SECRET_KEY"] = secret_key

    from aggregator.display_time import configured_timezone_name
    app.config["DISPLAY_TIMEZONE"] = configured_timezone_name()

    # Off by default: every page needs an account. On: signed-out visitors can
    # read Headlines, story pages and article summaries (aggregator/permissions.py).
    from aggregator.permissions import public_read_access_from_env
    app.config["PUBLIC_READ_ACCESS"] = public_read_access_from_env()

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)
    csrf.init_app(app)

    @login.user_loader
    def load_user(id):
        from aggregator.models import User
        user = User.query.get(int(id))
        # A disabled account loses any session it already had.
        return user if user and user.is_active else None

    @app.context_processor
    def inject_permissions():
        from aggregator.models import ROLE_LABELS
        from aggregator.permissions import can, public_read_enabled
        from aggregator import navigation
        from aggregator.article_copies import copy_label
        return {"can": can, "copy_label": copy_label, "public_read": public_read_enabled(), "role_labels": ROLE_LABELS,
                "current_back": navigation.current_back, "incoming_back": navigation.incoming_back,
                "feed_back_url": navigation.feed_back_url,
                "article_back_anchor": navigation.article_back_anchor, "menu_back": navigation.menu_back}

    @app.errorhandler(403)
    def forbidden(_error):
        from flask import render_template
        return render_template("forbidden.html"), 403

    from aggregator.filters import register_filters
    register_filters(app)

    from aggregator.blueprints.public import public
    from aggregator.blueprints.admin import admin
    from aggregator.blueprints.auth import auth
    app.register_blueprint(public)
    app.register_blueprint(admin,  url_prefix='/admin')
    app.register_blueprint(auth,   url_prefix='/auth')

    from aggregator import config_store
    config_store.install()

    return app


def create_db(app):
    with app.app_context():
        db.session.execute(db.text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.session.commit()
        db.create_all()

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


def create_app():
    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

    secret_key = os.environ.get("SECRET_KEY")
    if not secret_key:
        raise RuntimeError(
            "SECRET_KEY environment variable must be set (see .env.sample)"
        )
    app.config["SECRET_KEY"] = secret_key

    from aggregator.display_time import configured_timezone_name
    app.config["DISPLAY_TIMEZONE"] = configured_timezone_name()

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)
    csrf.init_app(app)

    @login.user_loader
    def load_user(id):
        from aggregator.models import User
        return User.query.get(int(id))

    from aggregator.filters import register_filters
    register_filters(app)

    from aggregator.blueprints.public import public
    from aggregator.blueprints.admin import admin
    from aggregator.blueprints.auth import auth
    app.register_blueprint(public)
    app.register_blueprint(admin,  url_prefix='/admin')
    app.register_blueprint(auth,   url_prefix='/auth')

    return app


def create_db(app):
    with app.app_context():
        db.session.execute(db.text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.session.commit()
        db.create_all()

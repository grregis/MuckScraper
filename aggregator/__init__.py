from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import LoginManager
import os
import logging

logger = logging.getLogger(__name__)

db = SQLAlchemy()
migrate = Migrate()
login = LoginManager()
login.login_view = "auth.login"


def create_app():
    app = Flask(__name__)

    app.config["SQLALCHEMY_DATABASE_URI"] = os.environ.get("DATABASE_URL", "")
    app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False
    app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-secret-key")

    db.init_app(app)
    migrate.init_app(app, db)
    login.init_app(app)

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
    app.register_blueprint(admin)
    app.register_blueprint(auth)

    try:
        from aggregator.blueprints.personal import personal
        app.register_blueprint(personal)
        logger.info("Personal 'Headlines' blueprint loaded.")
    except ImportError:
        pass

    return app


def create_db(app):
    with app.app_context():
        db.session.execute(db.text("CREATE EXTENSION IF NOT EXISTS vector"))
        db.session.commit()
        db.create_all()

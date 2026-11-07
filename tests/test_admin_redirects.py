from flask import Flask

from aggregator.blueprints.admin import admin
from aggregator.blueprints.admin._shared import redirect_to_articles


def test_redirect_to_articles_preserves_hidden_single_article_stories_from_referrer():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(admin, url_prefix="/admin")

    with app.test_request_context(
        "/admin/rate-article/1",
        method="POST",
        headers={"Referer": "http://localhost/admin/articles?show_single=false"},
    ):
        response = redirect_to_articles()

    assert response.location == "/admin/articles?show_single=false"

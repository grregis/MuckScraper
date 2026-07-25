import sys
import types

from flask import Flask

search = types.ModuleType("aggregator.search")
search.__dict__.update(
    SearchUnavailableError=Exception,
    reindex_all=lambda: None,
    search_story_ids=lambda query: [],
)
story_view = types.ModuleType("aggregator.story_view")
story_view.__dict__["apply_aggregator_filter"] = lambda story: None

sys.modules.setdefault("aggregator.search", search)
sys.modules.setdefault("aggregator.story_view", story_view)

from aggregator.blueprints.admin import admin, redirect_to_articles


def test_redirect_to_articles_preserves_single_article_view_from_referrer():
    app = Flask(__name__)
    app.secret_key = "test"
    app.register_blueprint(admin, url_prefix="/admin")

    with app.test_request_context(
        "/admin/rate-article/1",
        method="POST",
        headers={"Referer": "http://localhost/admin/articles?show_single=true"},
    ):
        response = redirect_to_articles()

    assert response.location == "/admin/articles?show_single=true"

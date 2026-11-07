"""Back-link helpers (2026-10-06): `back` must only ever be a same-site path."""
import pytest

from aggregator.navigation import safe_back


@pytest.mark.parametrize("value", [
    "/headlines",
    "/admin/multi-stories?range=30d&page=3",
    "/admin/search?q=senate&tab=articles&sort=date&dir=desc",
    "/admin/articles?topic=US+Politics&range=7d",
])
def test_same_site_paths_are_kept(value):
    assert safe_back(value) == value


@pytest.mark.parametrize("value", [
    None, "", "headlines", "https://evil.example/", "//evil.example/x",
    "/\\evil.example", "javascript:alert(1)", "/ok\r\nSet-Cookie: x=1", "/" + "a" * 2001,
])
def test_anything_else_is_refused(value):
    assert safe_back(value) is None


def _anchor_cases():
    from types import SimpleNamespace
    from flask import Flask
    from aggregator.navigation import article_back_anchor, feed_back_url
    app = Flask(__name__)
    app.add_url_rule("/headlines", "public.headlines_feed", lambda: "")
    out = {}
    with app.test_request_context("/stories/5?back=/admin/articles%3Frange%3D7d"):
        out["story"] = feed_back_url("story-5")
        out["article"] = article_back_anchor(SimpleNamespace(id=9, story_id=5))
    with app.test_request_context("/stories/5"):
        out["default"] = feed_back_url("story-5")
        out["none"] = feed_back_url()
    with app.test_request_context("/articles/9?back=/admin/search%3Fq%3Dx%26tab%3Darticles"):
        out["search_article"] = article_back_anchor(SimpleNamespace(id=9, story_id=5))
    with app.test_request_context("/stories/5?back=/admin/articles%23story-1"):
        out["has_fragment"] = feed_back_url("story-5")
    return out


def test_back_to_feed_lands_on_the_card():
    # 2026-10-13: "Back to Feed" opens the list at the card the reader left.
    c = _anchor_cases()
    assert c["story"] == "/admin/articles?range=7d#story-5"
    assert c["default"] == "/headlines#story-5"
    assert c["none"] == "/headlines"
    assert c["article"] == "story-5"
    assert c["search_article"] == "article-9"
    assert c["has_fragment"] == "/admin/articles#story-1"

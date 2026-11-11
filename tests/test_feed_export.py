"""
Tests for the feed export plugin system in MuckScraper.

Tests both the standalone XML / JSON generation and integration with
the public and admin blueprints (/headlines, /articles, /multi-stories).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import os
import xml.etree.ElementTree as ET
import pytest

from aggregator.feed_export import (
    is_feed_export_enabled,
    request_wants_feed,
    get_requested_feed_format,
    build_feed_url,
    format_rfc822,
    generate_rss_xml,
    generate_json_feed,
    render_feed_or_template,
)


# ============================================================================
# Unit Tests for feed_export functions
# ============================================================================

def test_is_feed_export_enabled():
    with patch.dict(os.environ, {"ENABLE_FEED_EXPORT": "true"}):
        assert is_feed_export_enabled() is True
    with patch.dict(os.environ, {"ENABLE_FEED_EXPORT": "false"}):
        assert is_feed_export_enabled() is False
    with patch.dict(os.environ, {}, clear=True):
        # Default is True
        assert is_feed_export_enabled() is True


def test_format_rfc822():
    dt = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)
    res = format_rfc822(dt)
    assert "Nov 2026" in res
    assert "12:00:00" in res

    # Naive datetime
    naive_dt = datetime(2026, 11, 5, 12, 0, 0)
    assert format_rfc822(naive_dt) == res

    # None falls back to current time
    now_res = format_rfc822(None)
    assert str(datetime.now().year) in now_res


def test_generate_rss_xml_structure():
    mock_outlet = SimpleNamespace(name="Associated Press", bias_score=3.0)
    mock_art = SimpleNamespace(
        id=1,
        title="Breaking News Wire",
        url="https://example.com/wire/1",
        outlet=mock_outlet,
        source="Associated Press",
        bias_score=3.0,
    )
    mock_topic = SimpleNamespace(name="US Politics")
    mock_story = SimpleNamespace(
        id=42,
        headline="Major Policy Shift Announced Today",
        title="Original Raw Headline",
        summary="Officials unveiled comprehensive reforms this afternoon.",
        created_at=datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc),
        summary_generated_at=datetime(2026, 11, 5, 10, 30, 0, tzinfo=timezone.utc),
        topics=[mock_topic],
        articles=[mock_art],
        left_bias_count=1,
        center_bias_count=2,
        right_bias_count=1,
    )

    xml_text = generate_rss_xml(
        stories=[mock_story],
        channel_title="MuckScraper — Morning Edition",
        channel_description="Curated top stories",
        channel_link="https://example.com/headlines",
        feed_url="https://example.com/headlines?format=rss",
    )

    assert xml_text.startswith('<?xml version="1.0" encoding="UTF-8"?>')
    root = ET.fromstring(xml_text)
    assert root.tag == "rss"
    assert root.attrib.get("version") == "2.0"

    channel = root.find("channel")
    assert channel is not None
    assert channel.find("title").text == "MuckScraper — Morning Edition"
    assert channel.find("link").text == "https://example.com/headlines"
    assert channel.find("description").text == "Curated top stories"

    items = channel.findall("item")
    assert len(items) == 1
    item = items[0]

    assert item.find("title").text == "Major Policy Shift Announced Today"
    assert "42" in item.find("link").text
    assert item.find("guid").text == item.find("link").text
    assert item.find("category").text == "US Politics"

    desc = item.find("description").text
    assert "Original Raw Headline" in desc
    assert "Officials unveiled comprehensive reforms" in desc
    assert "Coverage balance (4 sources)" in desc
    assert "Associated Press" in desc
    assert "Center" in desc


def test_generate_json_feed():
    from flask import Flask
    app = Flask(__name__)
    mock_topic = SimpleNamespace(name="Technology")
    mock_story = SimpleNamespace(
        id=99,
        headline="New Open-Source AI Breakthrough",
        title="New Open-Source AI Breakthrough",
        summary="Researchers release efficient model weights.",
        created_at=datetime(2026, 11, 5, 15, 0, 0, tzinfo=timezone.utc),
        summary_generated_at=None,
        topics=[mock_topic],
        articles=[],
    )

    with app.test_request_context():
        response = generate_json_feed(
            stories=[mock_story],
            channel_title="Tech Headlines",
            channel_description="Tech coverage",
            channel_link="https://example.com/tech",
            feed_url="https://example.com/tech.json",
        )
        data = response.get_json()

    assert data["version"] == "https://jsonfeed.org/version/1.1"
    assert data["title"] == "Tech Headlines"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["title"] == "New Open-Source AI Breakthrough"
    assert item["tags"] == ["Technology"]
    assert "Researchers release efficient model" in item["summary"]


# ============================================================================
# Integration Tests with Flask App & Test DB
# ============================================================================

@pytest.fixture
def app_with_db():
    os.environ["DATABASE_URL"] = "sqlite:///:memory:"
    os.environ["SECRET_KEY"] = "test-secret-key-1234567890abcdef1234567890abcdef"
    os.environ["PUBLIC_READ_ACCESS"] = "true"

    from aggregator import create_app, db
    from aggregator.models import Topic, Outlet, Story, Article

    app = create_app()
    app.config.update(
        TESTING=True,
        WTF_CSRF_ENABLED=False,
        PUBLIC_READ_ACCESS=True,
    )

    with app.app_context():
        db.create_all()

        outlet = Outlet(name="Global Gazette", url="https://example.com", description="N/A", bias_score=3.0)
        db.session.add(outlet)

        topic_politics = Topic(name="US Politics", is_active=True, sort_order=1)
        topic_science = Topic(name="Science", is_active=True, sort_order=2)
        db.session.add_all([topic_politics, topic_science])
        db.session.commit()

        # Story 1: Politics
        story1 = Story(
            headline="Senate Reaches Bipartisan Accord",
            title="Senate Agreement",
            summary="Key legislative breakthrough reached on budget.",
            headline_score=10.0,
            topics=[topic_politics],
        )
        db.session.add(story1)
        db.session.flush()

        art1a = Article(
            story_id=story1.id,
            outlet_id=outlet.id,
            title="Senate Agreement In Detail",
            url="https://example.com/senate-1",
            content="Full article body content",
            date=datetime.utcnow(),
        )
        art1b = Article(
            story_id=story1.id,
            outlet_id=outlet.id,
            title="Reactions to Senate Agreement",
            url="https://example.com/senate-2",
            content="Reaction details",
            date=datetime.utcnow(),
        )
        db.session.add_all([art1a, art1b])

        # Story 2: Science
        story2 = Story(
            headline="Deep Ocean Biodiversity Mapping Completed",
            title="Deep Ocean Biodiversity",
            summary="Submersible expedition catalogs rare hydrothermal vent species.",
            headline_score=8.0,
            topics=[topic_science],
        )
        db.session.add(story2)
        db.session.flush()

        art2a = Article(
            story_id=story2.id,
            outlet_id=outlet.id,
            title="Ocean Mission Success",
            url="https://example.com/ocean-1",
            content="Ocean mission article body",
            date=datetime.utcnow(),
        )
        art2b = Article(
            story_id=story2.id,
            outlet_id=outlet.id,
            title="New Marine Species Found",
            url="https://example.com/ocean-2",
            content="Marine biology details",
            date=datetime.utcnow(),
        )
        db.session.add_all([art2a, art2b])

        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


def test_headlines_feed_rss_format_param(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/headlines?format=rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]

    root = ET.fromstring(resp.data)
    assert root.tag == "rss"
    titles = [item.find("title").text for item in root.findall(".//item")]
    assert "Senate Reaches Bipartisan Accord" in titles
    assert "Deep Ocean Biodiversity Mapping Completed" in titles


def test_headlines_feed_rss_extension(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/headlines.rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"<channel>" in resp.data
    assert b"Senate Reaches Bipartisan Accord" in resp.data


def test_headlines_feed_feed_xml(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/headlines/feed.xml")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"Senate Reaches Bipartisan Accord" in resp.data


def test_headlines_feed_json_format(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/headlines?format=json")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["Content-Type"]
    data = resp.get_json()
    assert data["version"] == "https://jsonfeed.org/version/1.1"
    titles = [it["title"] for it in data["items"]]
    assert "Senate Reaches Bipartisan Accord" in titles

    resp_ext = client.get("/headlines.json")
    assert resp_ext.status_code == 200
    assert "application/json" in resp_ext.headers["Content-Type"]
    data_ext = resp_ext.get_json()
    assert data_ext["version"] == "https://jsonfeed.org/version/1.1"


def test_headlines_html_page_contains_autodiscovery_link(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/headlines")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["Content-Type"]
    # Check for RSS auto-discovery link tag in HTML
    html = resp.data.decode("utf-8")
    assert '<link rel="alternate" type="application/rss+xml"' in html
    assert 'format=rss' in html


def test_articles_topic_filter_preserves_selection_in_rss(app_with_db):
    client = app_with_db.test_client()

    # Request politics topic feed
    resp = client.get("/articles?topic=US+Politics&format=rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]

    root = ET.fromstring(resp.data)
    channel_title = root.find(".//channel/title").text
    assert "US Politics" in channel_title

    items = root.findall(".//item")
    assert len(items) == 1
    assert items[0].find("title").text == "Senate Reaches Bipartisan Accord"

    # Request science topic feed
    resp_sci = client.get("/articles?topic=Science&format=rss")
    assert resp_sci.status_code == 200
    root_sci = ET.fromstring(resp_sci.data)
    items_sci = root_sci.findall(".//item")
    assert len(items_sci) == 1
    assert items_sci[0].find("title").text == "Deep Ocean Biodiversity Mapping Completed"


def test_articles_rss_extension_with_topic_param(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/articles.rss?topic=US+Politics")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"Senate Reaches Bipartisan Accord" in resp.data
    assert b"Deep Ocean Biodiversity" not in resp.data


def test_multi_stories_rss(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/multi-stories.rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"<channel>" in resp.data


def test_admin_articles_rss(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/admin/articles?format=rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"<channel>" in resp.data

    resp2 = client.get("/admin/articles.rss")
    assert resp2.status_code == 200
    assert "application/rss+xml" in resp2.headers["Content-Type"]


def test_feeds_allowed_when_public_read_access_is_false(app_with_db):
    app_with_db.config["PUBLIC_READ_ACCESS"] = False
    client = app_with_db.test_client()

    # Browser request to HTML headlines should require login (302)
    html_resp = client.get("/headlines")
    assert html_resp.status_code == 302
    assert "/auth/login" in html_resp.headers["Location"]

    # Feed request should be allowed without login (200)
    rss_resp = client.get("/headlines.rss")
    assert rss_resp.status_code == 200
    assert "application/rss+xml" in rss_resp.headers["Content-Type"]

    rss_query_resp = client.get("/headlines?format=rss")
    assert rss_query_resp.status_code == 200

    topic_rss_resp = client.get("/articles.rss?topic=US+Politics")
    assert topic_rss_resp.status_code == 200


def test_feed_access_token_enforcement(app_with_db):
    app_with_db.config["PUBLIC_READ_ACCESS"] = False
    client = app_with_db.test_client()

    with patch.dict(os.environ, {"FEED_ACCESS_TOKEN": "secret-reader-token"}):
        # Without token, should be rejected / redirected to login
        unauth_resp = client.get("/headlines.rss")
        assert unauth_resp.status_code == 302

        # With wrong token, should be rejected
        wrong_resp = client.get("/headlines.rss?token=bad-token")
        assert wrong_resp.status_code == 302

        # With correct query token, succeeds
        auth_resp = client.get("/headlines.rss?token=secret-reader-token")
        assert auth_resp.status_code == 200
        assert "application/rss+xml" in auth_resp.headers["Content-Type"]

        # With Bearer header, succeeds
        header_resp = client.get("/headlines.rss", headers={"Authorization": "Bearer secret-reader-token"})
        assert header_resp.status_code == 200



"""
Tests for the feed export plugin system in MuckScraper.

Tests both the standalone XML / JSON generation and integration with
the public and admin blueprints (/headlines, /admin/articles, /admin/multi-stories).
"""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
import os
import xml.etree.ElementTree as ET
import pytest

from aggregator.feed_export import (
    is_feed_export_enabled,
    is_feed_token_authorized,
    request_wants_feed,
    get_requested_feed_format,
    build_feed_url,
    format_rfc822,
    format_rfc3339,
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


def test_format_rfc3339():
    dt = datetime(2026, 11, 5, 15, 30, 0, tzinfo=timezone.utc)
    res = format_rfc3339(dt)
    assert res == "2026-11-05T15:30:00+00:00"

    # Naive datetime is treated as UTC
    naive_dt = datetime(2026, 11, 5, 15, 30, 0)
    assert format_rfc3339(naive_dt) == "2026-11-05T15:30:00+00:00"


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

    atom_link = channel.find("{http://www.w3.org/2005/Atom}link")
    assert atom_link is not None
    assert atom_link.attrib.get("href") == "https://example.com/headlines?format=rss"
    assert atom_link.attrib.get("rel") == "self"

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


def test_generate_rss_xml_escapes_html_and_untrusted_urls():
    mock_outlet = SimpleNamespace(name='<img src=x onerror="alert(1)">Outlet', bias_score=3.0)
    mock_art_xss = SimpleNamespace(
        id=1,
        title='Evil <script>alert("title")</script>',
        url='"><script>alert("url")</script>',
        outlet=mock_outlet,
        source=None,
        bias_score=3.0,
    )
    mock_art_js = SimpleNamespace(
        id=2,
        title="JavaScript URL",
        url="javascript:alert(1)",
        outlet=SimpleNamespace(name="Safe News", bias_score=3.0),
        source=None,
        bias_score=3.0,
    )
    mock_story = SimpleNamespace(
        id=43,
        headline="Security Test",
        title='Story <script>alert("heading")</script>',
        summary="Safe summary",
        created_at=datetime(2026, 11, 5, 10, 0, 0, tzinfo=timezone.utc),
        summary_generated_at=None,
        topics=[],
        articles=[mock_art_xss, mock_art_js],
        left_bias_count=None,
        center_bias_count=None,
        right_bias_count=None,
    )

    xml_text = generate_rss_xml(
        stories=[mock_story],
        channel_title="Security Feed",
        channel_description="Desc",
        channel_link="https://example.com/headlines",
    )
    root = ET.fromstring(xml_text)
    desc = root.find(".//item/description").text

    # Verify no live unescaped script or img tags in description
    assert "<img" not in desc
    assert "&lt;img" in desc
    assert '<script>alert("url")' not in desc
    assert "<script>" not in desc
    # javascript: scheme URL must not be emitted as an active href link
    assert 'href="javascript:' not in desc
    assert "JavaScript URL" in desc


def test_generate_json_feed():
    from flask import Flask
    app = Flask(__name__)
    mock_topic = SimpleNamespace(name="Technology")
    mock_story = SimpleNamespace(
        id=99,
        headline="New Open-Source AI Breakthrough",
        title="New Open-Source AI Breakthrough",
        summary="Researchers release <script>alert('xss')</script><b>efficient</b> model weights.",
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
    assert data["feed_url"] == "https://example.com/tech.json"
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["title"] == "New Open-Source AI Breakthrough"
    assert item["tags"] == ["Technology"]
    # summary is cleaned text without HTML tags
    assert item["summary"] == "Researchers release alert('xss')efficient model weights."
    assert "<script>" not in item["content_html"]
    assert "<b>efficient</b>" in item["content_html"]
    # RFC 3339 date format for JSON Feed
    assert item["date_published"] == "2026-11-05T15:00:00+00:00"


# ============================================================================
# Integration Tests with Flask App & Test DB
# ============================================================================

@pytest.fixture
def app_with_db(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sqlite:///:memory:")
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-1234567890abcdef1234567890abcdef")
    monkeypatch.setenv("PUBLIC_READ_ACCESS", "true")

    from aggregator import create_app, db
    from aggregator.models import Topic, Outlet, Story, Article

    app = create_app()
    app.config.update(
        TESTING=True,
        SQLALCHEMY_DATABASE_URI="sqlite:///:memory:",
        WTF_CSRF_ENABLED=False,
        PUBLIC_READ_ACCESS=True,
    )

    with app.app_context():
        db.create_all()

        t_politics = Topic(name="US Politics", sort_order=1, is_active=True)
        t_science = Topic(name="Science", sort_order=2, is_active=True)
        db.session.add_all([t_politics, t_science])

        o1 = Outlet(name="AP News", bias_score=3.0)
        o2 = Outlet(name="Reuters", bias_score=3.0)
        db.session.add_all([o1, o2])
        db.session.flush()

        s1 = Story(
            headline="Senate Reaches Bipartisan Accord",
            title="Raw Senate Accord Title",
            summary="Negotiators agreed on a $1.2T federal framework after late-night talks.",
            created_at=datetime.now(timezone.utc),
            headline_score=9.5,
        )
        s1.topics.append(t_politics)

        s2 = Story(
            headline="Deep Ocean Biodiversity Mapping Completed",
            title="Ocean Biodiversity",
            summary="Scientists catalogued 5,000 new species in the abyssal zone.",
            created_at=datetime.now(timezone.utc),
            headline_score=8.0,
        )
        s2.topics.append(t_science)

        db.session.add_all([s1, s2])
        db.session.flush()

        a1 = Article(
            story_id=s1.id,
            outlet_id=o1.id,
            title="Senate passes bipartisan framework",
            url="https://example.com/senate",
            content="Full text",
            scrape_status="success",
        )
        a1b = Article(
            story_id=s1.id,
            outlet_id=o2.id,
            title="Reactions to Senate agreement",
            url="https://example.com/senate-reactions",
            content="Full reaction text",
            scrape_status="success",
        )
        a2 = Article(
            story_id=s2.id,
            outlet_id=o2.id,
            title="Ocean research voyage returns",
            url="https://example.com/ocean",
            content="Full text",
            scrape_status="success",
        )
        a2b = Article(
            story_id=s2.id,
            outlet_id=o1.id,
            title="Marine biologists catalog species",
            url="https://example.com/ocean-species",
            content="Full marine text",
            scrape_status="success",
        )
        db.session.add_all([a1, a1b, a2, a2b])
        db.session.commit()

        yield app

        db.session.remove()
        db.drop_all()


def test_headlines_feed_rss_format_query(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/headlines?format=rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"<rss " in resp.data
    assert b'version="2.0"' in resp.data
    assert b"Senate Reaches Bipartisan Accord" in resp.data


def test_headlines_feed_rss_extension(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/headlines.rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"<rss " in resp.data
    assert b'version="2.0"' in resp.data
    assert b"Senate Reaches Bipartisan Accord" in resp.data

    # Verify self-link is absolute
    root = ET.fromstring(resp.data)
    atom_link = root.find(".//channel/{http://www.w3.org/2005/Atom}link")
    assert atom_link is not None
    assert atom_link.attrib["href"].startswith("http://") or atom_link.attrib["href"].startswith("https://")


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
    assert data["feed_url"].startswith("http://") or data["feed_url"].startswith("https://")
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
    html_text = resp.data.decode("utf-8")
    assert '<link rel="alternate" type="application/rss+xml"' in html_text
    assert 'format=rss' in html_text
    assert 'href="http://' in html_text or 'href="https://' in html_text

    # Configured FEED_ACCESS_TOKEN must NOT leak into HTML page source
    with patch.dict(os.environ, {"FEED_ACCESS_TOKEN": "super-secret-token"}):
        resp_with_env = client.get("/headlines")
        assert "super-secret-token" not in resp_with_env.data.decode("utf-8")

        # But if the request explicitly arrived with ?token=..., it is preserved
        resp_with_param = client.get("/headlines?token=super-secret-token")
        assert "token=super-secret-token" in resp_with_param.data.decode("utf-8")


def test_admin_articles_topic_filter_preserves_selection_in_rss(app_with_db):
    client = app_with_db.test_client()

    # Request politics topic feed
    resp = client.get("/admin/articles?topic=US+Politics&format=rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]

    root = ET.fromstring(resp.data)
    channel_title = root.find(".//channel/title").text
    assert "US Politics" in channel_title

    items = root.findall(".//item")
    assert len(items) == 1
    assert items[0].find("title").text == "Senate Reaches Bipartisan Accord"

    # Request science topic feed
    resp_sci = client.get("/admin/articles?topic=Science&format=rss")
    assert resp_sci.status_code == 200
    root_sci = ET.fromstring(resp_sci.data)
    items_sci = root_sci.findall(".//item")
    assert len(items_sci) == 1
    assert items_sci[0].find("title").text == "Deep Ocean Biodiversity Mapping Completed"


def test_admin_articles_rss_extension_with_topic_param(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/admin/articles.rss?topic=US+Politics")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"Senate Reaches Bipartisan Accord" in resp.data
    assert b"Deep Ocean Biodiversity" not in resp.data


def test_admin_multi_stories_rss(app_with_db):
    client = app_with_db.test_client()
    resp = client.get("/admin/multi-stories.rss")
    assert resp.status_code == 200
    assert "application/rss+xml" in resp.headers["Content-Type"]
    assert b"<channel>" in resp.data


def test_feeds_require_login_when_public_read_access_is_false_without_token(app_with_db):
    app_with_db.config["PUBLIC_READ_ACCESS"] = False
    client = app_with_db.test_client()

    # Browser request to HTML headlines should require login (302)
    html_resp = client.get("/headlines")
    assert html_resp.status_code == 302
    assert "/auth/login" in html_resp.headers["Location"]

    # Without token configured or provided, feeds require login (302) like web pages
    rss_resp = client.get("/headlines.rss")
    assert rss_resp.status_code == 302
    assert "/auth/login" in rss_resp.headers["Location"]

    rss_query_resp = client.get("/headlines?format=rss")
    assert rss_query_resp.status_code == 302
    assert "/auth/login" in rss_query_resp.headers["Location"]

    topic_rss_resp = client.get("/admin/articles.rss?topic=US+Politics")
    assert topic_rss_resp.status_code == 302
    assert "/auth/login" in topic_rss_resp.headers["Location"]

    json_resp = client.get("/headlines.json")
    assert json_resp.status_code == 302
    assert "/auth/login" in json_resp.headers["Location"]


def test_feed_access_token_enforcement(app_with_db):
    app_with_db.config["PUBLIC_READ_ACCESS"] = False
    client = app_with_db.test_client()

    with patch.dict(os.environ, {"FEED_ACCESS_TOKEN": "secret-reader-token"}):
        # Without token, should be rejected / redirected to login
        unauth_resp = client.get("/headlines.rss")
        assert unauth_resp.status_code == 302
        assert "/auth/login" in unauth_resp.headers["Location"]

        # With wrong token, should be rejected
        wrong_resp = client.get("/headlines.rss?token=bad-token")
        assert wrong_resp.status_code == 302
        assert "/auth/login" in wrong_resp.headers["Location"]

        # With correct query token, succeeds
        auth_resp = client.get("/headlines.rss?token=secret-reader-token")
        assert auth_resp.status_code == 200
        assert "application/rss+xml" in auth_resp.headers["Content-Type"]

        # Topic filter with token succeeds
        topic_auth_resp = client.get("/admin/articles.rss?topic=US+Politics&token=secret-reader-token")
        assert topic_auth_resp.status_code == 200

        # With Bearer header, succeeds
        header_resp = client.get("/headlines.rss", headers={"Authorization": "Bearer secret-reader-token"})
        assert header_resp.status_code == 200


def test_token_does_not_open_non_feed_pages_when_public_read_off(app_with_db):
    app_with_db.config["PUBLIC_READ_ACCESS"] = False
    client = app_with_db.test_client()

    with patch.dict(os.environ, {"FEED_ACCESS_TOKEN": "secret-reader-token"}):
        # Attempting to access HTML story, article, or oped pages using ?format=rss&token=...
        # must still redirect to login (302) and NOT disclose HTML content.
        resp_story = client.get("/story/1?format=rss&token=secret-reader-token")
        assert resp_story.status_code == 302
        assert "/auth/login" in resp_story.headers["Location"]

        resp_article = client.get("/article/1?format=rss&token=secret-reader-token")
        assert resp_article.status_code == 302
        assert "/auth/login" in resp_article.headers["Location"]

        resp_oped = client.get("/admin/oped?format=rss&token=secret-reader-token")
        assert resp_oped.status_code == 302
        assert "/auth/login" in resp_oped.headers["Location"]


def test_non_ascii_token_does_not_raise_500(app_with_db):
    app_with_db.config["PUBLIC_READ_ACCESS"] = False
    client = app_with_db.test_client()

    with patch.dict(os.environ, {"FEED_ACCESS_TOKEN": "secret-reader-token"}):
        # Non-ASCII token in query parameter or Bearer header should be rejected safely without 500
        resp = client.get("/headlines.rss?token=t\u00e9")
        assert resp.status_code == 302
        assert "/auth/login" in resp.headers["Location"]

        header_resp = client.get("/headlines.rss", headers={"Authorization": "Bearer t\u00e9"})
        assert header_resp.status_code == 302


def test_headlines_feed_exports_entire_edition_without_pagination_limit(app_with_db):
    from datetime import date
    from aggregator import db
    from aggregator.models import Edition, EditionStory, Story, Article, Outlet

    with app_with_db.app_context():
        outlet = Outlet.query.first()

        # Create an edition with 15 stories (more than the 10 per_page limit)
        edition = Edition(
            date=date(2026, 11, 18),
            edition_type="morning",
            published=True,
        )
        db.session.add(edition)
        db.session.flush()

        outlet2 = Outlet.query.filter(Outlet.id != outlet.id).first()

        for i in range(1, 16):
            s = Story(
                headline=f"Edition Story #{i:02d}",
                title=f"Original Title #{i:02d}",
                summary=f"Summary for story {i}",
                created_at=datetime.now(timezone.utc),
                headline_score=float(100 - i),
            )
            db.session.add(s)
            db.session.flush()

            a1 = Article(
                story_id=s.id,
                outlet_id=outlet.id,
                title=f"Article 1 for Story {i}",
                url=f"https://example.com/story-{i}-1",
                scrape_status="success",
            )
            a2 = Article(
                story_id=s.id,
                outlet_id=outlet2.id if outlet2 else outlet.id,
                title=f"Article 2 for Story {i}",
                url=f"https://example.com/story-{i}-2",
                scrape_status="success",
            )
            db.session.add_all([a1, a2])

            es = EditionStory(
                edition_id=edition.id,
                story_id=s.id,
                rank=i,
            )
            db.session.add(es)

        db.session.commit()

    client = app_with_db.test_client()

    # Web (HTML) request should be paginated to per_page=10
    html_resp = client.get("/headlines")
    assert html_resp.status_code == 200
    assert b"Edition Story #01" in html_resp.data
    assert b"Edition Story #10" in html_resp.data
    assert b"Edition Story #11" not in html_resp.data

    # RSS feed request should include all 15 stories from the edition
    rss_resp = client.get("/headlines.rss")
    assert rss_resp.status_code == 200
    root = ET.fromstring(rss_resp.data)
    items = root.findall(".//item")
    assert len(items) == 15
    rss_titles = [it.find("title").text for it in items]
    assert "Edition Story #01" in rss_titles
    assert "Edition Story #15" in rss_titles

    # JSON feed request should also include all 15 stories
    json_resp = client.get("/headlines.json")
    assert json_resp.status_code == 200
    json_data = json_resp.get_json()
    assert len(json_data["items"]) == 15
    json_titles = [it["title"] for it in json_data["items"]]
    assert "Edition Story #01" in json_titles
    assert "Edition Story #15" in json_titles


def test_headlines_feed_exports_all_fallback_stories_without_pagination_limit(app_with_db):
    from aggregator import db
    from aggregator.models import Story, Article, Outlet

    with app_with_db.app_context():
        outlet = Outlet.query.first()
        outlet2 = Outlet.query.filter(Outlet.id != outlet.id).first()

        # Create 15 multi-article stories (no Edition published)
        for i in range(1, 16):
            s = Story(
                headline=f"Fallback Story #{i:02d}",
                title=f"Fallback Title #{i:02d}",
                summary=f"Summary for fallback story {i}",
                created_at=datetime.now(timezone.utc),
                headline_score=float(100 - i),
            )
            db.session.add(s)
            db.session.flush()

            a1 = Article(
                story_id=s.id,
                outlet_id=outlet.id,
                title=f"Fallback Art 1 #{i}",
                url=f"https://example.com/fb-{i}-1",
                scrape_status="success",
            )
            a2 = Article(
                story_id=s.id,
                outlet_id=outlet2.id if outlet2 else outlet.id,
                title=f"Fallback Art 2 #{i}",
                url=f"https://example.com/fb-{i}-2",
                scrape_status="success",
            )
            db.session.add_all([a1, a2])

        db.session.commit()

    client = app_with_db.test_client()

    # Web (HTML) request paginated to 10
    html_resp = client.get("/headlines")
    assert html_resp.status_code == 200
    assert b"Fallback Story #01" in html_resp.data
    assert b"Fallback Story #10" in html_resp.data
    assert b"Fallback Story #11" not in html_resp.data

    # RSS feed gets all 15 fallback stories (+ existing fixture stories up to 20 limit)
    rss_resp = client.get("/headlines.rss")
    assert rss_resp.status_code == 200
    root = ET.fromstring(rss_resp.data)
    items = root.findall(".//item")
    assert len(items) >= 15
    rss_titles = [it.find("title").text for it in items]
    assert "Fallback Story #01" in rss_titles
    assert "Fallback Story #15" in rss_titles

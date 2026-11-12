"""Which pages answer signed-out visitors (aggregator/permissions.py).

Walks the real URL map signed out, so a new route that forgets its
@role_required / @public_or_login decorator fails here. With
PUBLIC_READ_ACCESS off only login/logout may answer; with it on, exactly the
public reading pages may, and nothing else.
"""
from werkzeug.test import Client
from werkzeug.wrappers import Response

from aggregator.app import create_app

ALWAYS_OPEN = {"auth.login", "auth.logout", "static",
               # OIDC sign-in (#28), registered only when OIDC is on (production since 2026-10-29).
               "auth.oidc_login", "auth.oidc_callback",
                 # Return from provider sign-out (#40): checks a one-time state, then redirects to sign-in.
                 "auth.oidc_logout_callback"}
# Open to signed-out visitors only when PUBLIC_READ_ACCESS is on (2026-10-04).
PUBLIC_WHEN_ON = {
    "public.index", "public.headlines_feed", "public.view_story", "public.view_article",
    "public.public_articles", "public.public_multi_stories",  # feeds and stories aliases (2026-11-11)
    "admin.list_articles", "admin.multi_article_stories",
    "admin.oped_list",  # Op/Ed (2026-10-08), public like All Stories
    "admin.oped_piece",  # one Op/Ed piece (2026-10-16), no text signed out
    "public.ollama_status",
    "public.editions_archive", "public.edition_opinion",  # past editions, edition Opinion (2026-10-18)
}


def _signed_out_answers(public_read):
    app = create_app()
    app.config["PUBLIC_READ_ACCESS"] = public_read
    client = Client(app, Response)
    answered = set()
    for rule in app.url_map.iter_rules():
        if "GET" not in rule.methods or rule.endpoint in ALWAYS_OPEN:
            continue
        url = rule.build({arg: 1 for arg in rule.arguments}, append_unknown=False)[1]
        response = client.get(url, base_url="http://localhost")
        location = response.headers.get("Location", "")
        if not (response.status_code in (301, 302) and "/auth/login" in location):
            answered.add(rule.endpoint)
    return answered


def test_with_public_read_off_every_page_needs_login():
    assert _signed_out_answers(False) == set()


def test_with_public_read_on_only_the_public_reading_pages_answer():
    assert _signed_out_answers(True) == PUBLIC_WHEN_ON


def test_editions_by_month_groups_newest_first_morning_before_evening():
    from datetime import date
    from types import SimpleNamespace
    from aggregator.blueprints.public import editions_by_month

    eds = [
        SimpleNamespace(date=date(2026, 10, 2), edition_type="evening"),
        SimpleNamespace(date=date(2026, 10, 2), edition_type="morning"),
        SimpleNamespace(date=date(2026, 9, 30), edition_type="morning"),
    ]
    months = editions_by_month(eds)
    assert [m for m, _ in months] == ["October 2026", "September 2026"]
    day, editions = months[0][1][0]
    assert day == "Friday, October 02"
    assert [e.edition_type for e in editions] == ["morning", "evening"]

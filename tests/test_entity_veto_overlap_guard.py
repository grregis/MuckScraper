"""Entity-veto overlap guard (added 2026-09-27).

Cases are taken verbatim from real veto firings in the scheduler log, classified
by hand. The guard must suppress the false positives and preserve the two
firings confirmed correct. See entity_veto()'s docstring for the measurement.
"""
import pytest

from news_fetcher.story_grouper import entity_veto


class FakeStory:
    """Minimal stand-in for a Story row: entity_veto only reads these three."""

    def __init__(self, title, headline=None, article_titles=()):
        self.title = title
        self.headline = headline
        self.articles = [type("A", (), {"title": t})() for t in article_titles]


# --- Must NOT veto: same event, different country named -----------------------

FALSE_POSITIVES = [
    # The flagship case: one UN speech split from itself. Shares only
    # 'netanyahu' -- which is why a 2+ token threshold would not work.
    (
        "Fiery Netanyahu vows Iran's demise in heavily protested UN speech",
        FakeStory("Netanyahu faces new depths of isolation ahead of address to U.N.",
                  headline="Israel's Netanyahu heads to the UN amid isolation"),
    ),
    (
        "Israel's Benjamin Netanyahu denounces enemies, allies in fiery UN speech",
        FakeStory("Fiery Netanyahu vows Iran's demise in heavily protested UN speech"),
    ),
    # A summit split from itself: the article lists global conflicts because a UN
    # chief's speech does. No country allowlist could sensibly fix this one.
    (
        "From Gaza to Middle East, Ukraine, Sudan and beyond, our world needs peace, says UN chief at BRICS summit",
        FakeStory("BRICS leaders voice concern over Middle East, condemn unilateral sanctions at India summit"),
    ),
    # Single distinctive token, like Netanyahu.
    (
        "Japanese PM-backed candidate wins Okinawa gubernatorial election",
        FakeStory("Okinawa Set to Elect Conservative Amid Fears About China and Economy"),
    ),
    (
        "Anthems, transport, housing: What's gone wrong at the Asian Games in Japan?",
        FakeStory("South Korean officials decry 'worst ever' Asian Games accommodation"),
    ),
    (
        "Macklemore pledges $1 million to Palestinian relief, challenges Robert Kraft to match it",
        FakeStory("Who is Robert Kraft, pro-Israel billionaire who cancelled Macklemore?"),
    ),
    # One supply chain, described from either end.
    (
        "Has China received US F-35 parts by diverting an Australian shipment?",
        FakeStory("Australia investigating how F-35 fighter jet parts bound for US went missing in Hong Kong"),
    ),
]


@pytest.mark.parametrize("article_title,story", FALSE_POSITIVES)
def test_guard_suppresses_same_event_splits(article_title, story):
    assert entity_veto(article_title, story) is False


# --- Must STILL veto: genuinely unrelated, no shared vocabulary ---------------

TRUE_POSITIVES = [
    (
        "The Papers: 'Monstrous words' and 'Russia to ramp up war'",
        FakeStory("Earl Spencer's unreleased book on sister Diana stolen in the Netherlands"),
    ),
    (
        "American Israeli held hostage by Hamas performs songs of survival and hope",
        FakeStory("Ed Sheeran apologizes for 'making mistakes,' says situation in Gaza 'unjustifiable'"),
    ),
]


@pytest.mark.parametrize("article_title,story", TRUE_POSITIVES)
def test_guard_preserves_genuine_vetoes(article_title, story):
    assert entity_veto(article_title, story) is True


# --- Pre-existing behaviour that must not regress -----------------------------

def test_no_veto_when_article_names_no_country():
    assert entity_veto(
        "Mike Rogers speaks out after skipping Michigan Senate debate",
        FakeStory("Nato jets down drone that entered Lithuanian airspace"),
    ) is False


def test_no_veto_when_story_names_no_country():
    assert entity_veto(
        "Iran rejects peace talks",
        FakeStory("Local council approves new bus lane"),
    ) is False


def test_no_veto_when_countries_overlap():
    assert entity_veto(
        "Iran rejects peace talks with Washington",
        FakeStory("Iran signals openness to negotiation"),
    ) is False
